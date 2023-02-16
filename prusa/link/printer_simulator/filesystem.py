from typing import List


class FileNode:
    def __init__(self, name, short_name):
        self.name = name
        self.short_name = short_name


class File(FileNode):

    def __init__(self, name, short_name, modified_time, size):
        super().__init__(name, short_name)
        self.modified_time = modified_time
        self.size = size

    def __str__(self):
        return f"{self.short_name} {self.size} {hex(int(self.modified_time))} \"{self.name}\""


class Directory(FileNode):

    def __init__(self, name, short_name):
        super().__init__(name, short_name)
        self.children: List[FileNode] = []

    def _str_children(self):
        return [str(node) for node in self.children]

    def __str__(self):
        output = [f"DIR_ENTER: /{self.name.upper()[:8]}/ \"{self.name}\""]
        output.extend(self._str_children())
        output.append("DIR_EXIT")
        return "\n".join(output)


class FileTree(Directory):
    def __init__(self):
        super().__init__("ROOT", "ROOT")

    def __str__(self):
        output = ["Begin file list"]
        output.extend(self._str_children())
        output.append("End file list")
        return "\n".join(output)
