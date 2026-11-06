# Endless Legend macOS VisualFix

Restores movement path colors and turn numbers in the native macOS version of
**Endless Legend 1**. Also repairs the same shader defect in lava rivers.

Movement path colors and turn numbers have been visually confirmed on Apple
Silicon. Lava river rendering has not yet been visually confirmed.

## Requirements

- The native macOS game, using Unity 5.3 and OpenGL.
- Python 3.9 or newer.
- Write access to the game folder and enough free space for an original backup
and a temporary resource file, approximately twice the resource file's size.

No Unity editor, Mono, Homebrew, or additional Python packages are required.

## Installation

1. Quit Endless Legend.
2. Clone this repository, or choose **Code → Download ZIP** on GitHub and extract
   the downloaded source code.
3. Open Terminal in the repository folder and run:

```sh
python3 visual_fix.py install
```

Start the game normally. No special launch options are needed.

The default game folder is:

```text
~/Library/Application Support/Steam/steamapps/common/Endless Legend
```

For a different Steam library, pass the game folder or application bundle:

```sh
python3 visual_fix.py install --game "/path/to/Endless Legend"
python3 visual_fix.py install --game "/path/to/EndlessLegend.app"
```

You can inspect the installation plan without changing files:

```sh
python3 visual_fix.py install --dry-run
```

Keep `visual_fix.py` and the `elvisualfix` folder together. The downloaded folder
can be located outside the game directory.

## Status and verification

```sh
python3 visual_fix.py status
python3 visual_fix.py verify
```

`status` lists each target shader as `needs-fix`, `installed`, or
`already-corrected`. `already-corrected` means its recognized programs are
repaired, but the shader is not marked as owned by this installer. An absent
lava shader is reported as `not present` and does not prevent installation.

`verify` checks the Unity file structure and recognized repaired shader code.
Its exit status is `0` when no repair is needed, `2` when affected programs
remain, and `1` for an unsupported file, unsupported shader code, or an error.

## Removal

Quit the game and run:

```sh
python3 visual_fix.py uninstall
```

Use the same `--game` option if the game is installed in another location.
Restart the game after removing the fix.

The installer keeps recovery files inside the game's `VisualFix` folder.
Keep that folder until the fix has been removed. Each original resource file
has its own backup, identified by its SHA-256. Installation receipts retain the
original shader objects and identify the corresponding installed objects.

If the resource file has not changed since installation, removal restores its
original bytes exactly. If unrelated assets were changed or added afterward,
removal locates the installed shader objects again and restores only those
objects, preserving the other assets. If an installed shader itself was changed,
or its recovery information is missing, removal stops instead of replacing it
with unrelated old data. Backups are retained after removal.

## DLC, updates, and other modifications

Installation does not depend on a particular DLC list, whole-file checksum,
resource file size, shader object ID, object offset, or variant count. The
installer reads the current Unity object table and locates the target shaders
by their full names. Different asset orders, additional objects, and missing
optional lava assets are supported.

After installing DLC or updating the game, run `status`. If Steam replaced the
shaders, run `install` again. New original resource files receive separate
backups. Do not copy an old complete resource backup over newly installed DLC.

The supported serialized file format is little-endian Unity format 15 with
the native game's Unity 5.3 shader layout. Both included and stripped type
trees are supported. Recognized GLSL program contents are checked individually;
an unfamiliar program or file format causes installation to stop before game
resources are changed. This targets the native game rather than arbitrary Unity
applications or future engine conversions.

## How it works

The affected GLSL programs read an unassigned `vec3` local variable in a
specular lighting calculation. The undefined value can contaminate the final
RGB output, making path indicators and lava appear black.

The installer repairs these shaders in
`EndlessLegend.app/Contents/Resources/Data/resources.assets`:

- `Amplitude/Characters/PathFind`
- `Custom/RiverLava`, when present

For each target object, it decodes the LZ4 program blob, identifies recognized
GLSL programs, and replaces the undefined specular product with `vec3(0.0)`.
Removing the full product also avoids leaving `0 * pow(0, 0)` in the expression.
All program string lengths and offsets inside the decoded blob stay unchanged.
Material colors, textures, alpha, and defined view and fog calculations are
preserved.

The corrected blob is encoded as a valid raw LZ4 block using a literal sequence.
This avoids native compression dependencies and adds approximately 90 KiB to
a resource file containing the standard shader variants. The installer rebuilds
the shader objects and updates Unity's object offsets, object sizes, and file
size to account for the change. Other serialized object bytes, external resource
files, object IDs, and references remain unchanged.

A complete candidate resource file is written and validated first. Recovery
data is saved before an atomic file replacement. The installer checks that the
source file has not changed during preparation. It makes no changes to managed
DLLs, game rules, saved games, or audio playback.

The lava target draws flowing lava. Separate smoke and steam particle effects
are outside this patch's scope.

## Tests

The code uses Python's standard library. Run the portable regression suite with:

```sh
python3 -m unittest discover -v
```

Tests cover different asset layouts and IDs, optional shaders, repeated shader
objects, variable program counts, type trees, object relocation, installation
idempotency, rollback after asset changes, malformed data, interrupted writes,
and concurrent file updates. Test fixtures are synthetic and contain no game
assets.

To check every unrelated object and an exact installation/removal cycle against
a locally owned original game file:

```sh
python3 tools/check_game.py "/path/to/original/resources.assets"
```

This uses a temporary copy and does not modify the supplied file.

## License

The installer and synthetic tests are distributed under the MIT license.
Endless Legend and its assets belong to their respective owners. Game assets
are not included in the repository.
