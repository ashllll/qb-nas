#!/usr/bin/env python3
"""SSH into NAS and check qBittorrent permissions"""
import paramiko, json, sys

host, port = '192.168.1.69', 5666
user, pw = 'root', 'AshKJ1280!!'

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(host, port=port, username=user, password=pw, timeout=15)

def run(cmd):
    stdin, stdout, stderr = client.exec_command(cmd, timeout=15)
    return stdout.read().decode().strip()

# 1. Find qB container
print("=== Docker ps ===")
r = run("docker ps --format 'table {{.ID}}\t{{.Names}}\t{{.Image}}' | head -20")
print(r)

cid = run("docker ps --format '{{.Names}}' | grep -iE 'qbit|bittorrent|qb' | head -1")
if not cid:
    print("\n(qB container not found by name, using all)")
else:
    print(f"\n=== Container: {cid} ===")

    print("\n--- Container user ---")
    print(run(f"docker exec {cid} id"))

    print("\n--- Mounts ---")
    m = run(f"""docker inspect {cid} | python3 -c "
import sys,json
d=json.load(sys.stdin)[0]
for m in d.get('Mounts', []):
    print(f\"{m['Type']}: {m.get('Source','?')} -> {m.get('Destination','?')}\")" """)
    print(m)

    print("\n--- Check /Download permissions inside container ---")
    print(run(f"docker exec {cid} ls -la /Download/ | head -10"))

    print("\n--- df -h ---")
    print(run(f"docker exec {cid} df -h /Download"))

    print("\n--- whoami inside container ---")
    print(run(f"docker exec {cid} whoami"))

client.close()
