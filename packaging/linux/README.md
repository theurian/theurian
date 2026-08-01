# Linux packaging

## systemd user unit

Installed at `~/.config/systemd/user/theurian.service`. A **user** unit: no root,
no `/etc`, and it respects the XDG base directory specification.

Skeleton (rendered by the `DaemonManager` adapter):

```ini
[Unit]
Description=Theurian knowledge daemon
Documentation=https://github.com/theurian/theurian
After=network.target

[Service]
Type=simple
ExecStart={{ theurian_path }} daemon start --foreground
Restart=on-failure
RestartSec=5
Environment=THEURIAN_DATA_DIR={{ data_dir }}

# The daemon reads project files and writes only its own data directory.
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths={{ data_dir }}
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
MemoryMax=2G

[Install]
WantedBy=default.target
```

`ProtectHome=read-only` with an explicit `ReadWritePaths` is the interesting
part: it means a path-traversal bug that slipped past the application-level
containment check still cannot write outside the data directory. Defence in depth
for T-4 and T-5.

## Commands

```sh
systemctl --user daemon-reload
systemctl --user enable --now theurian.service
systemctl --user status theurian.service
journalctl --user -u theurian.service -f
systemctl --user disable --now theurian.service
```

## Lingering

By default a user unit stops when the last session ends. For a daemon that should
survive logout:

```sh
loginctl enable-linger "$USER"
```

Setup does **not** enable lingering automatically. It changes how the machine
behaves after logout, which is the user's decision, not an installer's.

## Distributions and architectures

Tested on Ubuntu 24.04 and Fedora 41, `x86_64` and `aarch64`. Any distribution
with systemd 250+ and Python 3.13 should work.

Without systemd (some containers, some minimal distributions), run
`theurian daemon start --foreground` under whatever supervisor is present.
`DaemonManager.is_supported()` reports this correctly rather than failing
mysteriously.
