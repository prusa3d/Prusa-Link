"""
Contains implementation of the HeapItem, MinHeap and MaxHeap classes
I HAVE COPIED THIS FROM THE INTERNET!
It turns out that popping the last item in the queue was broken
"""
from typing import List


class HeapItem:
    """An item in the heap. Needs to be comparable"""
    def __init__(self, value):
        self.value = value
        self.heap_value = None
        self.heap_index = None

    def __gt__(self, other):
        if isinstance(other, HeapItem):
            return self.heap_value > other.heap_value
        raise TypeError("HeapItems can be compared only with each other")

    def __ge__(self, other):
        if isinstance(other, HeapItem):
            return self.heap_value >= other.heap_value
        raise TypeError("HeapItems can be compared only with each other")

    def __lt__(self, other):
        if isinstance(other, HeapItem):
            return self.heap_value < other.heap_value
        raise TypeError("HeapItems can be compared only with each other")

    def __le__(self, other):
        if isinstance(other, HeapItem):
            return self.heap_value <= other.heap_value
        raise TypeError("HeapItems can be compared only with each other")

    def __eq__(self, other):
        if isinstance(other, HeapItem):
            return self.heap_value == other.heap_value
        raise TypeError("HeapItems can be compared only with each other")


class MinHeap:
    """Min heap implementation with element adding and removing"""
    def __init__(self) -> None:
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
        """Ads an element to the heap"""
        item.heap_value = item.value
        self._push(item)

    def _push(self, item):
        """
        Adds an element to the heap

        In min heaps this is done by adding the element at the end, then
        switching places with parents larger than it
        """
        self.heap.append(item)

        initial_index = len(self.heap) - 1
        self.sift_down(0, initial_index)

    def pop(self, index: int = 0) -> HeapItem:
        """
        Removes an element from the heap

        In min heaps this is done by swapping the element with the last
        element in the heap, then removing the last element, (which is now
        the thing we wanted to delete). After that depending on the value of
        the element that replaced the deleted one sifting it up or down
        """

        old_item: HeapItem = self.heap[index]
        old_value = old_item.heap_value

        if old_item.heap_index != index:
            raise RuntimeError("Item index and actual index differ. NOOOOO!")

        new_item = self.heap.pop()

        # The first one checks if we didn't remove the last item from the
        # heap, if we did, there is nothing else that needs to be done
        if index != len(self) and self:
            new_value = new_item.heap_value

            self.heap[index] = new_item
            if new_value > old_value:
                self.sift_up(index)
            else:
                self.sift_down(0, index)

        return old_item

    def sift_up(self, pos):
        """
        Compares an element with its children, if the element is larger,
        its position gets swapped with the smaller child. Continues until
        there are no children smaller than the element
        """
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
        # The leaf at pos is empty now. Put newitem there, and bubble it up
        # to its final resting place (by sifting its parents down).
        self.heap[pos] = newitem
        self.heap[pos].heap_index = pos
        self.sift_down(startpos, pos)

    def sift_down(self, startpos, pos):
        """
        The element gets compared with its parent, if it's smaller, they get
        swapped. Continues until it finds a parent smaller than itself,
        or the element becomes the root of the heap
        :param startpos:
        :param pos:
        :return:
        """
        newitem = self.heap[pos]
        # Follow the path to the root, moving parents down until finding
        # a place newitem fits.
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
    """
    Lazily implemented max heap by using the min heap, just inverting
    the heap value
    """
    def push(self, item: HeapItem):
        item.heap_value = -item.value
        self._push(item)
