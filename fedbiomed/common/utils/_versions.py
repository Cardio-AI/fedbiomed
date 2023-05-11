# This file is originally part of Fed-BioMed
# SPDX-License-Identifier: Apache-2.0

"""Utility functions for handling version compatibility.

This module contains functions that help managing the versions of different Fed-BioMed components.

See https://fedbiomed.org/latest/user-guide/deployment/versions for more information

!!! info "Instructions for developers"
    If you make a change that changes the format/metadata/structure of one of the components below,
    you **must update** the version.

Components concerned by versioning:

- config files (researcher and node)

Instructions for updating the version

1. bump the version in common/constants.py: if your change breaks backward compatibility you must increase the
    major version, else the minor version. Micro versions are supported but their use is currently discouraged.
2. Update the [Changelog page](https://fedbiomed.org/latest/user-guide/deployment/versions) with a short description
    of your change, ideally including instructions on how to manually migrate from the previous version.

"""

from packaging.version import Version
from typing import Optional, Union
from fedbiomed.common.logger import logger
from fedbiomed.common.exceptions import FedbiomedVersionError


FBM_Component_Version = Version  # for Typing
__default_version__ = Version('0')  # default version to assign to any component before versioning was introduced


def _create_msg_for_version_check(error_msg: str,
                                  their_version: FBM_Component_Version,
                                  our_version: FBM_Component_Version) -> str:
    """Utility function to put together a nice error message when versions don't exactly match.

    Args:
        error_msg: customizable part of the message. It may contain two %s placeholders which will be substituted with
            the values of their_version and our_version.
        their_version: the version that we detected in the component
        our_version: the version of the component within the current runtime

    Returns:
        A formatted message with a link to the docs appended at the end.
    """
    try:
        msg = error_msg % (str(their_version), str(our_version))
    except TypeError:
        msg = error_msg
    msg += " -> See https://fedbiomed.org/latest/user-guide/deployment/versions for more information"
    return msg


def raise_for_version_compatibility(their_version: Union[FBM_Component_Version, str],
                                    our_version: Union[FBM_Component_Version, str],
                                    error_msg: Optional[str] = None) -> None:
    """Check version compatibility and behave accordingly.

    Raises an exception if the versions are incompatible, otherwise outputs a warning or info message.

    Args:
        their_version: the version that we detected in the component
        our_version: the version of the component within the current runtime
        error_msg: an optional error message. It may contain two %s placeholders which will be substituted with
            the values of their_version and our_version.

    Raises:
        FedbiomedVersionError: if the versions are incompatible
    """
    if isinstance(our_version, str):
        our_version = FBM_Component_Version(our_version)
    if isinstance(their_version, str):
        their_version = FBM_Component_Version(their_version)
    if our_version != their_version:
        msg = _create_msg_for_version_check(
            "Found version %s, expected version %s" if error_msg is None else error_msg,
            their_version,
            our_version
        )
        # note: the checks below rely on the short-circuiting behaviour of the or operator
        # (e.g. when checking our_version.minor < their_version.minor we have the guarantee that
        # our_version.major == their_version.major
        if our_version.major != their_version.major or \
                our_version.minor < their_version.minor or \
                (our_version.minor == their_version.minor and our_version.micro < their_version.micro):
            logger.critical(msg)
            raise FedbiomedVersionError(msg)
        else:
            logger.warning(msg)
