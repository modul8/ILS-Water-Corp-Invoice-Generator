# Releasing Windows Builds (GitHub Actions)

This repo builds Windows installers on **tags** that start with `v`.

## One-time setup
1) GitHub Pages:
   - Repo → Settings → Pages → Source: **GitHub Actions**
2) Make sure the update URL is:
   - `https://modul8.github.io/ILS-Water-Corp-Invoice-Generator/updates/latest.json`

## Release steps
1) Commit your changes to `dev` and push.
2) Create and push a tag:

```bash
git tag v2.1.1
git push origin v2.1.1
```

3) Wait for the workflow to complete:
   - Actions → “Release Windows Build”

## Outputs
- GitHub Release with installer asset:
  - `ILS_WaterCorp_Invoice_Generator_Setup.exe`
- GitHub Pages update feed:
  - `updates/latest.json`

## Notes
- The workflow sets `APP_VERSION` and Inno Setup version to match the tag.
- Local builds should also update `APP_VERSION` for consistency.
