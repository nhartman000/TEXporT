# Publishing TExporT to the Snap Store

Everything below runs on your Ubuntu machine. The store account and name belong to you, or to American Milestone Inc.

## 0. One-time setup
```bash
sudo snap install snapcraft --classic
sudo snap install lxd && sudo lxd init --auto && sudo usermod -aG lxd "$USER"   # log out/in after this
```
Create an Ubuntu One account at https://snapcraft.io (use the American Milestone email if the company should own the listing).

## 1. Build locally — do NOT use `snapcraft remote-build`
remote-build uploads the source to Launchpad, where it is public. For a proprietary app, always build on your own machine:
```bash
cd texport
snapcraft            # produces texport_1.0.0_amd64.snap (first build takes a while)
```

## 2. Test the snap before releasing
```bash
sudo snap install --dangerous ./texport_1.0.0_amd64.snap
texport
```
Check this list:
1. Share… shows Ubuntu's screen-share dialog, and capture works.
2. The text is read correctly.
3. Export writes .md, .pdf and the .mg8 folder.
4. Verify… works.

If screen sharing fails inside the snap but works with `install.sh`, it's a confinement issue. Send me the output of `snap run texport` and `journalctl --user -e | grep -i -E "texport|portal|pipewire"`.

## 3. Register the name and upload
```bash
snapcraft login
snapcraft register texport
snapcraft upload --release=stable texport_1.0.0_amd64.snap
```
Start on `--release=edge` if you'd like a quiet test channel first. Install it from there with `sudo snap install texport --edge`.

## 4. Store listing (snapcraft.io → My snaps → texport → Listing)
- **Title:** TExporT. **License:** Proprietary (already set in snapcraft.yaml).
- **Screenshots:** the floating panel over Claude, the marked lines, the export PDF, and the Verify result showing a FAIL with missing markers. That last one is the MG8 hook.
- **Website:** americanmilestone.com, or the MG8 repo.

## Snap and the nych / mg8-engine integration — decision needed
A strict snap can't execute programs or load compiled libraries from your home folder, so a strict TExporT snap can't drive a separately installed nych/mg8-engine. Capture, mark, export and Verify all work fine in the strict snap. Options for the pipeline handoff:
1. **Classic confinement.** The snap runs like a normal app and can call your workspace. Canonical has to approve it by manual review; "drives the user's locally installed toolchain" is the usual justification.
2. **Bundle nych + mg8-engine inside the snap.** Both are MIT, so this is allowed with notices. It works out of the box, but it's no longer the "install them separately" workflow.
3. **Use install.sh / a .deb for the integrated workflow,** and keep the strict snap for capture-only users.

## Notes
- Proprietary means the license, not hidden code. The Python files are readable inside any snap. If you want them harder to read later, ship compiled `.pyc` only. That deters casual copying, not determined reverse engineering.
- Updates: bump `version` in `snap/snapcraft.yaml`, rebuild, then upload again.
