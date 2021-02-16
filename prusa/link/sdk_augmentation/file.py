from pathlib import Path

from prusa.connect.printer.files import File


class SDFile(File):
    def add_file_from_line(self, line: str):

        path, str_size = line.rsplit(" ", 1)
        size = int(str_size)

        self.add_by_path(path=path, size=size)

    def add_node(self, is_dir, path: Path, name, **attrs):
        parts = Path(path).parts
        # Ignores the first "/"
        node: SDFile = self.get(parts[1:])
        if node is None:
            raise FileNotFoundError(f"Can't find the node at {path} to add"
                                    f" the child named {name} to.")
        else:
            node.add(is_dir=is_dir, name=name, ro=True, **attrs)

    def add_directory(self, path: Path, name, **attrs):
        self.add_node(True, path, name, **attrs)

    def add_file(self, path, name, **attrs):
        self.add_node(False, path, name, **attrs)
