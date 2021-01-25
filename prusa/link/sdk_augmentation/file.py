from pathlib import Path

from prusa.connect.printer.files import File


class SDFile(File):

    def add_file_from_line(self, line: str):

        path, str_size = line.rsplit(" ", 1)
        size = int(str_size)

        self.add_by_path(path=path, size=size)

    def add_by_path(self, path, size):
        parts = Path(path).parts

        node: SDFile = self
        for part in parts[1:-1]:
            if part not in node.children:
                node.add(is_dir=True, name=part, ro=True)

            node = node.get([part])

        node.add(is_dir=False, name=parts[-1], size=size, ro=True)