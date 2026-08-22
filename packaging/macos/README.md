# macOS packaging

## LaunchAgent

Installed at `~/Library/LaunchAgents/dev.theurian.daemon.plist`. A **LaunchAgent**,
not a LaunchDaemon: agents run as the logged-in user, need no root, and stop when
the user logs out. Theurian serves one user's knowledge; it has no business
running as a system service.

Skeleton (rendered by the `DaemonManager` adapter):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>              <string>dev.theurian.daemon</string>
  <key>ProgramArguments</key>
  <array>
    <string>{{ theurian_path }}</string>
    <string>daemon</string>
    <string>start</string>
    <string>--foreground</string>
  </array>
  <key>RunAtLoad</key>          <true/>
  <key>KeepAlive</key>
  <dict><key>SuccessfulExit</key><false/></dict>
  <key>ProcessType</key>        <string>Background</string>
  <key>StandardOutPath</key>    <string>{{ data_dir }}/logs/daemon.log</string>
  <key>StandardErrorPath</key>  <string>{{ data_dir }}/logs/daemon.err</string>
</dict>
</plist>
```

`KeepAlive` restarts on crash but not after a clean exit, so `theurian daemon
stop` actually stops it rather than fighting launchd.

## Commands

```sh
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/dev.theurian.daemon.plist
launchctl kickstart -k gui/$(id -u)/dev.theurian.daemon
launchctl print gui/$(id -u)/dev.theurian.daemon
launchctl bootout gui/$(id -u)/dev.theurian.daemon
```

## Distribution

A binary distributed outside the App Store must be signed and notarized, or
Gatekeeper blocks it with an error most users read as "this software is
malware". The alternative is `uv tool install --python 3.13 'theurian[daemon]'`
or `pipx install --python 3.13 'theurian[daemon]'`, which sidesteps Gatekeeper
entirely and is the recommended path for
developers. The extra is named because a bare install has no `uvicorn`, and
this page is about getting a *daemon* onto a Mac.

## Architectures

`arm64` (Apple silicon) and `x86_64` (Intel). Both are release gates at 1.0.
