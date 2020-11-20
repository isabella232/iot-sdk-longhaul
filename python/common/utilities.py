# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE file in the project root for
# full license information.
import string
import random


def get_random_string(length):
    """
    return a random string of `length` characters.
    """
    return "".join(random.choice(string.ascii_uppercase + string.digits) for _ in range(length))


def get_random_length_string(length):
    """
    return a random string with a random length of `length` characters at most.
    """
    return "".join(
        random.choice(string.ascii_uppercase + string.digits)
        for _ in range(random.randint(1, length))
    )
