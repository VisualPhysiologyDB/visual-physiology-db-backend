Mol* viewer 5.13.0 — https://molstar.org/ — MIT licence in LICENSE.
Source release: https://github.com/molstar/molstar/releases/tag/v5.13.0
Pinned distribution: https://registry.npmjs.org/molstar/-/molstar-5.13.0.tgz

The viewer JS and CSS were taken from package/build/viewer/. The only local
modification removes trailing sourceMappingURL comments because debug source
maps are not shipped. This lets Django/WhiteNoise collect and hash the assets.
Upstream archive, upstream file and local file checksums are recorded in
data/tuning/downloads.json at the repository root.
No npm scripts are run and the browser never resolves an unpinned Mol* version.

Keep this notice and the licence when redistributing. Updating the viewer is a
reviewed dependency change; rerun browser tests for residue selection, images,
local uploads and session restoration before replacing it.
