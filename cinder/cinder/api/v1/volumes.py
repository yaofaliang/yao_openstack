# Copyright 2011 Justin Santa Barbara
# All Rights Reserved.
#
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

"""The volumes api."""

import ast

from oslo_log import log as logging
from oslo_utils import uuidutils
import webob
from webob import exc

from cinder.api import common
from cinder.api.openstack import wsgi
from cinder.i18n import _, _LI
from cinder import objects
from cinder.objects import fields
from cinder import utils
from cinder import volume as cinder_volume
from cinder.volume import utils as volume_utils


LOG = logging.getLogger(__name__)


def _translate_attachment_detail_view(_context, vol):
    """Maps keys for attachment details view."""

    d = _translate_attachment_summary_view(_context, vol)

    # No additional data / lookups at the moment

    return d


def _translate_attachment_summary_view(_context, vol):
    """Maps keys for attachment summary view."""
    d = []
    attachments = vol.volume_attachment
    for attachment in attachments:
        if (attachment.get('attach_status') ==
                fields.VolumeAttachStatus.ATTACHED):
            a = {'id': attachment.get('volume_id'),
                 'attachment_id': attachment.get('id'),
                 'volume_id': attachment.get('volume_id'),
                 'server_id': attachment.get('instance_uuid'),
                 'host_name': attachment.get('attached_host'),
                 'device': attachment.get('mountpoint'),
                 }
            d.append(a)

    return d


def _translate_volume_detail_view(context, vol, image_id=None):
    """Maps keys for volumes details view."""

    d = _translate_volume_summary_view(context, vol, image_id)

    # No additional data / lookups at the moment

    return d


def _translate_volume_summary_view(context, vol, image_id=None):
    """Maps keys for volumes summary view."""
    d = {}

    d['id'] = vol['id']
    d['status'] = vol['status']
    d['size'] = vol['size']
    d['availability_zone'] = vol['availability_zone']
    d['created_at'] = vol['created_at']

    # Need to form the string true/false explicitly here to
    # maintain our API contract
    if vol['bootable']:
        d['bootable'] = 'true'
    else:
        d['bootable'] = 'false'

    if vol['multiattach']:
        d['multiattach'] = 'true'
    else:
        d['multiattach'] = 'false'

    d['attachments'] = []
    if vol['attach_status'] == fields.VolumeAttachStatus.ATTACHED:
        d['attachments'] = _translate_attachment_detail_view(context, vol)

    d['display_name'] = vol['display_name']
    d['display_description'] = vol['display_description']

    if vol['volume_type_id'] and vol.get('volume_type'):
        d['volume_type'] = vol['volume_type']['name']
    else:
        d['volume_type'] = vol['volume_type_id']

    d['snapshot_id'] = vol['snapshot_id']
    d['source_volid'] = vol['source_volid']

    d['encrypted'] = vol['encryption_key_id'] is not None

    if image_id:
        d['image_id'] = image_id

    LOG.info(_LI("vol=%s"), vol)

    if vol.metadata:
        d['metadata'] = vol.metadata
    else:
        d['metadata'] = {}

    return d


class VolumeController(wsgi.Controller):
    """The Volumes API controller for the OpenStack API."""

    def __init__(self, ext_mgr):
        self.volume_api = cinder_volume.API()
        self.ext_mgr = ext_mgr
        super(VolumeController, self).__init__()

    def show(self, req, id):
        """Return data about the given volume."""
        context = req.environ['cinder.context']

        # Not found exception will be handled at the wsgi level
        vol = self.volume_api.get(context, id, viewable_admin_meta=True)
        req.cache_db_volume(vol)

        utils.add_visible_admin_metadata(vol)

        return {'volume': _translate_volume_detail_view(context, vol)}

    def delete(self, req, id):
        """Delete a volume."""
        context = req.environ['cinder.context']

        LOG.info(_LI("Delete volume with id: %s"), id)

        # Not found exception will be handled at the wsgi level
        volume = self.volume_api.get(context, id)
        self.volume_api.delete(context, volume)
        return webob.Response(status_int=202)

    def index(self, req):
        """Returns a summary list of volumes."""
        return self._items(req, entity_maker=_translate_volume_summary_view)

    def detail(self, req):
        """Returns a detailed list of volumes."""
        return self._items(req, entity_maker=_translate_volume_detail_view)

    def _items(self, req, entity_maker):
        """Returns a list of volumes, transformed through entity_maker."""

        # pop out limit and offset , they are not search_opts
        search_opts = req.GET.copy()
        search_opts.pop('limit', None)
        search_opts.pop('offset', None)

        for k, v in search_opts.items():
            try:
                search_opts[k] = ast.literal_eval(v)
            except (ValueError, SyntaxError):
                LOG.debug('Could not evaluate value %s, assuming string', v)

        context = req.environ['cinder.context']
        utils.remove_invalid_filter_options(context,
                                            search_opts,
                                            self._get_volume_search_options())

        volumes = self.volume_api.get_all(context, marker=None, limit=None,
                                          sort_keys=['created_at'],
                                          sort_dirs=['desc'],
                                          filters=search_opts,
                                          viewable_admin_meta=True)

        for volume in volumes:
            utils.add_visible_admin_metadata(volume)

        limited_list = common.limited(volumes.objects, req)
        req.cache_db_volumes(limited_list)

        res = [entity_maker(context, vol) for vol in limited_list]
        return {'volumes': res}

    def _image_uuid_from_href(self, image_href):
        # If the image href was generated by nova api, strip image_href
        # down to an id.
        try:
            image_uuid = image_href.split('/').pop()
        except (TypeError, AttributeError):
            msg = _("Invalid imageRef provided.")
            raise exc.HTTPBadRequest(explanation=msg)

        if not uuidutils.is_uuid_like(image_uuid):
            msg = _("Invalid imageRef provided.")
            raise exc.HTTPBadRequest(explanation=msg)

        return image_uuid

    def create(self, req, body):
        """Creates a new volume."""
        if not self.is_valid_body(body, 'volume'):
            raise exc.HTTPUnprocessableEntity()

        LOG.debug('Create volume request body: %s', body)
        context = req.environ['cinder.context']
        volume = body['volume']

        kwargs = {}

        req_volume_type = volume.get('volume_type', None)
        if req_volume_type:
            # Not found exception will be handled at the wsgi level
            kwargs['volume_type'] = (
                objects.VolumeType.get_by_name_or_id(context, req_volume_type))

        kwargs['metadata'] = volume.get('metadata', None)

        snapshot_id = volume.get('snapshot_id')
        if snapshot_id is not None:
            if not uuidutils.is_uuid_like(snapshot_id):
                msg = _("Snapshot ID must be in UUID form.")
                raise exc.HTTPBadRequest(explanation=msg)
            # Not found exception will be handled at the wsgi level
            kwargs['snapshot'] = self.volume_api.get_snapshot(context,
                                                              snapshot_id)
        else:
            kwargs['snapshot'] = None

        source_volid = volume.get('source_volid')
        if source_volid is not None:
            # Not found exception will be handled at the wsgi level
            kwargs['source_volume'] = self.volume_api.get_volume(context,
                                                                 source_volid)
        else:
            kwargs['source_volume'] = None

        size = volume.get('size', None)
        if size is None and kwargs['snapshot'] is not None:
            size = kwargs['snapshot']['volume_size']
        elif size is None and kwargs['source_volume'] is not None:
            size = kwargs['source_volume']['size']

        LOG.info(_LI("Create volume of %s GB"), size)
        multiattach = volume.get('multiattach', False)
        kwargs['multiattach'] = multiattach

        image_href = None
        image_uuid = None
        if self.ext_mgr.is_loaded('os-image-create'):
            # NOTE(jdg): misleading name "imageRef" as it's an image-id
            image_href = volume.get('imageRef')
            if image_href is not None:
                image_uuid = self._image_uuid_from_href(image_href)
                kwargs['image_id'] = image_uuid

        kwargs['availability_zone'] = volume.get('availability_zone', None)

        new_volume = self.volume_api.create(context,
                                            size,
                                            volume.get('display_name'),
                                            volume.get('display_description'),
                                            **kwargs)

        retval = _translate_volume_detail_view(context, new_volume, image_uuid)

        return {'volume': retval}

    def _get_volume_search_options(self):
        """Return volume search options allowed by non-admin."""
        return ('display_name', 'status', 'metadata')

    def update(self, req, id, body):
        """Update a volume."""
        context = req.environ['cinder.context']

        if not body:
            raise exc.HTTPUnprocessableEntity()

        if 'volume' not in body:
            raise exc.HTTPUnprocessableEntity()

        volume = body['volume']
        update_dict = {}

        valid_update_keys = (
            'display_name',
            'display_description',
            'metadata',
        )

        for key in valid_update_keys:
            if key in volume:
                update_dict[key] = volume[key]

        # Not found exception will be handled at the wsgi level
        volume = self.volume_api.get(context, id, viewable_admin_meta=True)
        volume_utils.notify_about_volume_usage(context, volume,
                                               'update.start')
        self.volume_api.update(context, volume, update_dict)

        volume.update(update_dict)

        utils.add_visible_admin_metadata(volume)

        volume_utils.notify_about_volume_usage(context, volume,
                                               'update.end')

        return {'volume': _translate_volume_detail_view(context, volume)}


def create_resource(ext_mgr):
    return wsgi.Resource(VolumeController(ext_mgr))