# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE file in the project root for
# full license information.
import string
import random


def get_random_string(length):
    return "".join(random.choice(string.ascii_uppercase + string.digits) for _ in range(length))
