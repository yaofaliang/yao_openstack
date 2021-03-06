# -*- coding: utf-8 -*-
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import inspect
import logging
import logging.config
import logging.handlers
import os
import six
try:
    import syslog
except ImportError:
    syslog = None
from oslo_utils import encodeutils


NullHandler = logging.NullHandler


def _get_binary_name():
    return os.path.basename(inspect.stack()[-1][1])


_AUDIT = logging.INFO + 1
_TRACE = 5


if syslog is not None:
    class OSSysLogHandler(logging.Handler):
        """Syslog based handler. Only available on UNIX-like platforms."""
        severity_map = {
            "CRITICAL": syslog.LOG_CRIT,
            "DEBUG": syslog.LOG_DEBUG,
            "ERROR": syslog.LOG_ERR,
            "INFO": syslog.LOG_INFO,
            "WARNING": syslog.LOG_WARNING,
            "WARN": syslog.LOG_WARNING,
        }

        def __init__(self, facility=syslog.LOG_USER):
            # Do not use super() unless type(logging.Handler) is 'type'
            # (i.e. >= Python 2.7).
            logging.Handler.__init__(self)
            binary_name = _get_binary_name()
            syslog.openlog(binary_name, 0, facility)

        def emit(self, record):
            priority = self.severity_map.get(record.levelname,
                                             syslog.LOG_DEBUG)
            message = self.format(record)

            # NOTE(gangila): In python2, the syslog function takes in 's' as
            # the format argument, which means it either accepts python string
            # (str = 'a') or unicode strings (str = u'a'), the PyArg_ParseTuple
            # then if needed converts the unicode objects to C strings using
            # the *default encoding*. This default encoding is 'ascii' in case
            # of python2 while it has been changed to 'utf-8' in case of
            # python3. What this leads to is when we supply a syslog message
            # like:
            # >>> syslog.syslog(syslog.LOG_DEBUG, u"François Deppierraz")
            # In case of python2 the above fails with TypeError: [priority,]
            # message string. Because python2 doesn't explicitly encode as
            # 'utf-8' and use the system default encoding ascii, which raises
            # a UnicodeEncodeError (UnicodeEncodeError: 'ascii' codec can't
            # encode character u'\xe7' in position 4: ordinal not in
            # range(128)), and hence the error message that's set in the code
            # (TypeError: [priority,] message string) gets shown to the user.
            # However, this in the case of Python3, where the system default
            # encoding is 'utf-8' works without any issues. Therefore, we need
            # to safe_encode in case of python2 and not in case of Python3.
            if six.PY2:
                message = encodeutils.safe_encode(self.format(record))

            syslog.syslog(priority, message)


class ColorHandler(logging.StreamHandler):
    LEVEL_COLORS = {
        _TRACE: '\033[00;35m',  # MAGENTA
        logging.DEBUG: '\033[00;32m',  # GREEN
        logging.INFO: '\033[00;36m',  # CYAN
        _AUDIT: '\033[01;36m',  # BOLD CYAN
        logging.WARN: '\033[01;33m',  # BOLD YELLOW
        logging.ERROR: '\033[01;31m',  # BOLD RED
        logging.CRITICAL: '\033[01;31m',  # BOLD RED
    }

    def format(self, record):
        record.color = self.LEVEL_COLORS[record.levelno]
        return logging.StreamHandler.format(self, record)
