# ReachWeaver brand assets

Original vector artwork for the ReachWeaver Claude Code social-media skill. The mark is **two interwoven signal paths with four endpoints**: one verified source can become distinct, approved, platform-native outputs. It does not imply connections, live publishing or reach guarantees on any network.

| File | Usage |
| --- | --- |
| [icon.svg](icon.svg) | Canonical square logo, scalable SVG, works from ~32 px upwards, dark tile included |
| [lockup-light.svg](lockup-light.svg) | Icon + wordmark, transparent background, **light backgrounds only** |
| [social-preview.svg](social-preview.svg) | Editable 1280 × 640 GitHub repository social preview source |

## Colors

- Midnight background: `#111A33` → `#080D1D`
- Aqua thread: `#72F1DA` → `#37DCCC` → `#329DEB`
- Iris thread: `#789DFF` → `#A587F9` → `#DA7DE5`
- Light text: `#F6FAFF`
- Teal wordmark accent (on light): `#159B99`

## Export and use

Keep SVG as the vector source. Raster exports are optional; export to **1024 × 1024** or **512 × 512** for social avatars, and **1280 × 640** for GitHub social preview.

With Inkscape (optional):

```sh
inkscape assets/brand/icon.svg --export-filename=reachweaver-icon-1024.png --export-width=1024 --export-height=1024
inkscape assets/brand/social-preview.svg --export-filename=reachweaver-social-preview.png --export-width=1280 --export-height=640
```

GitHub does **not** automatically use repository image files for its social preview. Upload the exported PNG manually in the repo's **Settings → General → Social preview**, if available to your account. The README displays the SVG independently of that setting.

Maintain clear space around the mark, do not redraw/alter social-platform trademarks, avoid placing the dark-text lockup on dark surfaces, and use alt text such as “ReachWeaver: two colorful interwoven signal paths.” SVGs are self-contained; text remains editable using the specified DejaVu Sans/Arial fallback font stack. Image assets are not evidence of software capability.
