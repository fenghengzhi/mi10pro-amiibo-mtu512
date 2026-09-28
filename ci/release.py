#!/usr/bin/env python3
"""Prepare/publish one immutable-by-convention build record, never overwrite history."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import zipfile


def context(env):
    rules = {
        'GITHUB_REPOSITORY': r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',
        'GITHUB_SHA': r'[0-9a-f]{40}',
        'GITHUB_RUN_ID': r'[1-9][0-9]*',
        'GITHUB_RUN_NUMBER': r'[1-9][0-9]*',
        'GITHUB_RUN_ATTEMPT': r'[1-9][0-9]*',
    }
    for key, pattern in rules.items():
        if not re.fullmatch(pattern, env.get(key, '')):
            raise ValueError(f'Missing or invalid {key}')
    if env.get('GITHUB_REF') != 'refs/heads/main':
        raise ValueError('Only builds of main can be published')
    return {key: env[key] for key in rules}


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prepare(root, env, now=None):
    ctx = context(env)
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    dist = root / 'dist'
    packages = list(dist.glob('*.zip'))
    if len(packages) != 1:
        raise ValueError('Expected exactly one newly built Magisk ZIP')
    package = packages[0]
    with zipfile.ZipFile(package) as archive:
        if archive.testzip() is not None:
            raise ValueError('ZIP integrity check failed')
        props = dict(line.split('=', 1) for line in archive.read('module.prop').decode().splitlines() if '=' in line)
        review = json.loads(archive.read('static-review.json'))
        payload_sha = hashlib.sha256(archive.read('payload/libbluetooth_qti.so')).hexdigest()
        if payload_sha != review['patched_sha256']:
            raise ValueError('Packaged payload differs from its reviewed hash')
    tag = f"build-{now:%Y%m%dT%H%M%SZ}-{ctx['GITHUB_RUN_ID']}-{ctx['GITHUB_RUN_ATTEMPT']}"
    build_url = f"https://github.com/{ctx['GITHUB_REPOSITORY']}/actions/runs/{ctx['GITHUB_RUN_ID']}/attempts/{ctx['GITHUB_RUN_ATTEMPT']}"
    info = {
        'tag': tag,
        'built_at_utc': now.strftime('%Y-%m-%dT%H:%M:%SZ'),
        'repository': ctx['GITHUB_REPOSITORY'],
        'commit': ctx['GITHUB_SHA'],
        'run_id': ctx['GITHUB_RUN_ID'],
        'run_number': ctx['GITHUB_RUN_NUMBER'],
        'run_attempt': ctx['GITHUB_RUN_ATTEMPT'],
        'workflow_url': build_url,
        'module_version': props['version'],
        'module_version_code': int(props['versionCode']),
        'firmware_fingerprint': review['firmware_fingerprint'],
        'package': package.name,
        'package_sha256': sha256(package),
        'payload_sha256': payload_sha,
        'ci_checks': 'Static ELF/instruction checks, ARM64 emulation, module guards, builder and verifier tests',
        'device_tested_by_this_ci_run': False,
    }
    info_path = dist / 'build-info.json'
    info_path.write_text(json.dumps(info, indent=2) + '\n')
    (dist / 'SHA256SUMS.txt').write_text(''.join(f'{sha256(p)}  {p.name}\n' for p in [package, info_path]))
    title = f"{now:%Y-%m-%d %H:%M:%S} UTC · MTU 512 {props['version']} · #{ctx['GITHUB_RUN_NUMBER']}.{ctx['GITHUB_RUN_ATTEMPT']}"
    body = f"""自动构建的 Magisk 安装包。请下载 Assets 中的 **`{package.name}`**；GitHub 自动生成的 Source code ZIP 不是安装包。

仅支持小米 10 Pro（cmi）、`V816.0.9.0.TJACNXM`、Android 13 / SDK 33、Magisk 30.7。

- 构建时间：{now:%Y-%m-%d %H:%M:%S UTC}
- 模块版本：`{props['version']}`（versionCode {props['versionCode']}）
- 源码提交：[`{ctx['GITHUB_SHA'][:12]}`](https://github.com/{ctx['GITHUB_REPOSITORY']}/commit/{ctx['GITHUB_SHA']})
- 构建和测试日志：[Actions #{ctx['GITHUB_RUN_NUMBER']}.{ctx['GITHUB_RUN_ATTEMPT']}]({build_url})
- 安装包 SHA-256：`{info['package_sha256']}`

本次 CI 完成静态检查、ARM64 模拟执行和模块保护测试；不代表这次构建已经在手机或 Switch 上实测。既有实机证据和安装限制见[验证报告](https://github.com/{ctx['GITHUB_REPOSITORY']}/blob/{ctx['GITHUB_SHA']}/VALIDATION.zh-CN.md)与[安装说明](https://github.com/{ctx['GITHUB_REPOSITORY']}/blob/{ctx['GITHUB_SHA']}/INSTALL.zh-CN.md)。

每次成功构建创建独立 Release，重跑也不覆盖历史包。[全部构建记录（从新到旧）](https://github.com/{ctx['GITHUB_REPOSITORY']}/releases)
"""
    (root / 'release-notes.md').write_text(body)
    (root / 'release-title.txt').write_text(title)
    return info


def api(endpoint, payload):
    result = subprocess.run(['gh', 'api', '--method', 'POST', endpoint, '--input', '-'],
                            input=json.dumps(payload), text=True, capture_output=True, check=True)
    return json.loads(result.stdout)


def publish(root, env):
    ctx = context(env)
    dist = root / 'dist'
    info = json.loads((dist / 'build-info.json').read_text())
    if any(info[field] != ctx[key] for field, key in [
        ('repository', 'GITHUB_REPOSITORY'), ('commit', 'GITHUB_SHA'),
        ('run_id', 'GITHUB_RUN_ID'), ('run_attempt', 'GITHUB_RUN_ATTEMPT')]):
        raise ValueError('Prepared release belongs to another workflow run')
    package = dist / info['package']
    if package.parent != dist or sha256(package) != info['package_sha256']:
        raise ValueError('Prepared package has changed')
    repo, tag = info['repository'], info['tag']
    # Fresh annotated tag dates keep repeated builds of the same commit in
    # build order on Releases. Never update an existing tag or release.
    tag_object = api(f'repos/{repo}/git/tags', {
        'tag': tag, 'message': f"Magisk build {info['run_number']}.{info['run_attempt']}",
        'object': info['commit'], 'type': 'commit',
        'tagger': {'name': 'github-actions[bot]',
                   'email': '41898282+github-actions[bot]@users.noreply.github.com',
                   'date': info['built_at_utc']},
    })
    api(f'repos/{repo}/git/refs', {'ref': f'refs/tags/{tag}', 'sha': tag_object['sha']})
    subprocess.run(['gh', 'release', 'create', tag, '--repo', repo, '--verify-tag', '--draft',
                    '--title', (root / 'release-title.txt').read_text(),
                    '--notes-file', str(root / 'release-notes.md'),
                    str(package), str(dist / 'SHA256SUMS.txt'), str(dist / 'build-info.json')], check=True)
    # Publish only after all three attachments have uploaded successfully.
    subprocess.run(['gh', 'release', 'edit', tag, '--repo', repo, '--draft=false', '--latest'], check=True)
    url = f'https://github.com/{repo}/releases/tag/{tag}'
    if env.get('GITHUB_STEP_SUMMARY'):
        with open(env['GITHUB_STEP_SUMMARY'], 'a') as summary:
            summary.write(f'Published [{tag}]({url})\n\nSHA-256: `{info["package_sha256"]}`\n')
    print(url)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['prepare', 'publish'])
    args = parser.parse_args()
    {'prepare': prepare, 'publish': publish}[args.command](Path.cwd(), os.environ)
