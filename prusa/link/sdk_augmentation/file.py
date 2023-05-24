"""Contains implementation of the SDFile class which augments the SDK File"""
from pathlib import Path

from prusa.connect.printer.files import File


class SDFile(File):
    """Adds a few useful methods for adding SD Files parsed from serial"""

    def add_node(self, is_dir, path: Path, name, sfn, **attrs):
        """
        Adds a file/dir node to a path, can add only into an existing dir
        node
        """
        parts = Path(path).parts
        # Ignores the first "/"
        node: "SDFile" = self.get(parts[1:])
        if not str(path).startswith("/."):
            if node is None:
                raise FileNotFoundError(f"Can't find the node at {path} to add"
                                        f" the child named {name} to.")
            node.add(is_dir=is_dir, name=name, read_only=True, sfn=sfn,
                     **attrs)

    def add_directory(self, path: Path, name, sfn, **attrs):
        """Shorthand for adding directories"""
        self.add_node(True, path, name, sfn=sfn, **attrs)

    def add_file(self, path, name, sfn, **attrs):
        """Shorthand for adding files"""
        self.add_node(False, path, name, sfn=sfn, **attrs)
