from typing import List


class HeapItem:
    def __init__(self, value):
        self.value = value
        self.heap_value = None
        self.heap_index = None

    def __gt__(self, other: "HeapItem"):
        return self.heap_value > other.heap_value

    def __ge__(self, other: "HeapItem"):
        return self.heap_value >= other.heap_value

    def __lt__(self, other: "HeapItem"):
        return self.heap_value < other.heap_value

    def __le__(self, other: "HeapItem"):
        return self.heap_value <= other.heap_value

    def __eq__(self, other: "HeapItem"):
        return self.heap_value == other.heap_value


class MinHeap:

    def __init__(self):
        self.heap: List[HeapItem] = []

    def __len__(self):
        return len(self.heap)

    def __bool__(self):
        return bool(self.heap)

    def __getitem__(self, key):
        return self.heap[key]

    def __setitem__(self, key, value):
        self.heap[key] = value

    def push(self, item: HeapItem):
        item.heap_value = item.value
        self._push(item)

    def _push(self, item):
        self.heap.append(item)

        initial_index = len(self.heap) - 1
        self.sift_down(0, initial_index)

    def pop(self, index=0):
        old_item: HeapItem = self.heap[index]
        old_value = old_item.heap_value

        if old_item.heap_index != index:
            raise RuntimeError("Item index and actual index differ. NOOOOO!")

        new_item = self.heap.pop()

        if self:
            new_value = new_item.heap_value

            if index > len(self) - 1:
                index = len(self) - 1

            self.heap[index] = new_item
            if new_value > old_value:
                self.sift_up(index)
            else:
                self.sift_down(0, index)

        return old_item

    def sift_up(self, pos):
        endpos = len(self.heap)
        startpos = pos
        newitem = self.heap[pos]
        # Bubble up the smaller child until hitting a leaf.
        childpos = 2 * pos + 1  # leftmost child position
        while childpos < endpos:
            # Set childpos to index of smaller child.
            rightpos = childpos + 1
            if rightpos < endpos and \
                    not self.heap[childpos] < self.heap[rightpos]:
                childpos = rightpos
            # Move the smaller child up.
            self.heap[pos] = self.heap[childpos]
            self.heap[pos].heap_index = pos
            pos = childpos
            childpos = 2 * pos + 1
        # The leaf at pos is empty now.  Put newitem there, and bubble it up
        # to its final resting place (by sifting its parents down).
        self.heap[pos] = newitem
        self.heap[pos].heap_index = pos
        self.sift_down(startpos, pos)

    def sift_down(self, startpos, pos):
        newitem = self.heap[pos]
        # Follow the path to the root, moving parents down until finding a place
        # newitem fits.
        while pos > startpos:
            parentpos = (pos - 1) >> 1
            parent = self.heap[parentpos]
            if newitem < parent:
                self.heap[pos] = parent
                parent.heap_index = pos
                pos = parentpos
                continue
            break
        self.heap[pos] = newitem
        newitem.heap_index = pos


class MaxHeap(MinHeap):

    def push(self, item: HeapItem):
        item.heap_value = -item.value
        self._push(item)
