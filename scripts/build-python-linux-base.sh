# Copyright (c) Microsoft. All rights reserved.
# Licensed under the MIT license. See LICENSE file in the project root for
# full license information.
set -e
script_dir=$(cd "$(dirname "$0")" && pwd)

source ${script_dir}/_get_base_env
for lang in ${ALL_LINUX_LANGUAGES}; do 
    ${script_dir}/_build_base.sh  linux $lang
done

