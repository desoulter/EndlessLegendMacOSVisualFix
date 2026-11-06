# Endless Legend macOS VisualFix 1.0.0

Restores colored movement path indicators and movement-turn numbers in the
native macOS version of Endless Legend 1. Repairs the same undefined-color
calculation in the lava river shader.

- Locates shaders by name in the installed resource file.
- Supports different DLC asset layouts without fixed file hashes or offsets.
- Requires only Python 3.9 or newer, with no additional packages.
- Creates original backups and supports removal after unrelated asset changes.
- Preserves other serialized objects, managed assemblies, saved games, and audio.

Movement path colors and turn numbers are visually confirmed on Apple Silicon.
Lava river rendering is not yet visually confirmed.

Clone the repository or download the source code, quit the game, and run
`python3 visual_fix.py install`. No build step is required.
Use `python3 visual_fix.py uninstall` to remove it. See README.md for custom
game locations, verification, compatibility, and the implementation details.
