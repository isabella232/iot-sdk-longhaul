# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE file in the project root for
# full license information.
import threading
import logging


logger = logging.getLogger("thief.{}".format(__name__))


class OutOfOrderMessageTracker(object):
    """
    Object to help us keep track of missing messages.

    We can only tell that message x is missing if we receive message x+1, but we don't receive message x.

    """

    def __init__(self):
        """
        max_resovled_index is the index where every message before it has been received.
        max_received_index is the highest index received.
        unresolved_indices is the list of indexes received that are > max_resolved

        So, if we receive 1,2,3, 5, and 7:
        max_resolved_index will be 3 (because we've received up until 3, but we haven't received 4)
        max_received_index will be 7 (because it's the biggest index we've received)
        unresovled_indices will be [5, 7] because these are indices that we've received that are > 3
        """
        self.max_resolved_index = None
        self.max_received_index = 0
        self.unresolved_indices = set()
        self.lock = threading.Lock()

    def add_message(self, index):
        """
        Add a message to our tracker.  This just adds the message index to a list of received messages.
        This function has the side effect of removing received messages from our list if they are
        the "expected next message", so the received message list only needs to be used if we're
        missing any messages.
        """

        with self.lock:
            self.unresolved_indices.add(index)

            if index > self.max_received_index:
                self.max_received_index = index

            if self.max_resolved_index is None:
                self.max_resolved_index = index - 1

            while len(self.unresolved_indices):
                if (self.max_resolved_index + 1) in self.unresolved_indices:
                    self.max_resolved_index += 1
                    self.unresolved_indices.remove(self.max_resolved_index)
                else:
                    break

        logger.info(
            "After adding {}, received={}, resolved={}, unresolved={}".format(
                index, self.max_received_index, self.max_resolved_index, self.unresolved_indices
            )
        )

    def get_missing_count(self):
        """
        My brain can't explain this right now, but you can try this with the example above.
        This will return 2, which is correct since we're missing 4 and 6.
        """
        with self.lock:
            if self.max_resolved_index is None:
                return 0
            else:
                return (
                    self.max_received_index - self.max_resolved_index - len(self.unresolved_indices)
                )
