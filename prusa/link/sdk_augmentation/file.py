from pathlib import Path

from prusa.connect.printer.files import File


class SDFile(File):
    """Adds a few useful methods for adding SD Files parsed from serial"""
    def add_node(self, is_dir, path: Path, name, **attrs):
        """
        Adds a file/dir node to a path, can add only into an existing dir
        node
        """
        parts = Path(path).parts
        # Ignores the first "/"
        node: SDFile = self.get(parts[1:])
        if node is None:
            raise FileNotFoundError(f"Can't find the node at {path} to add"
                                    f" the child named {name} to.")
        node.add(is_dir=is_dir, name=name, ro=True, **attrs)

    def add_directory(self, path: Path, name, **attrs):
        """Shorthand for adding directories"""
        self.add_node(True, path, name, **attrs)

    def add_file(self, path, name, **attrs):
        """Shorthand for adding files"""
        self.add_node(False, path, name, **attrs)
