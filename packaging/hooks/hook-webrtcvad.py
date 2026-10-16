# The contrib hook copies metadata for "webrtcvad", but requirements.txt installs the module from
# the webrtcvad-wheels distribution, so that lookup fails the build. This hook takes precedence.
from PyInstaller.utils.hooks import copy_metadata

datas = copy_metadata("webrtcvad-wheels")
