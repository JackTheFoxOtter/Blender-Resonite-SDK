Blender Resonite SDK
====================

[![License: GPL-3.0](https://img.shields.io/badge/License-GPL3.0-yellow)](https://opensource.org/license/gpl-3.0)

Resonite SDK for Blender.

# ⚠️ Alpha WARNING! ⚠️
This SDK is in early development and still missing a lot. I'm not yet accepting pull requests.

# Building
Blender extensions require all dependencies to be bundled with the extension.
For this, the Python .whl files of the required modules are bundled in the `.\wheels` directory.
Pip can be used to download the required wheels specified in the `.\requirements.txt` file using the following command:

```
pip download -r .\requirements.txt -d .\wheels
```

The blender extension distribution archive can be created into the `.\dist` using the following blender command (assuming blender's install directory is in your terminal's search path):
```
blender --command extension build --output-dir .\dist
```