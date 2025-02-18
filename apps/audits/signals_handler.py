# -*- coding: utf-8 -*-
#
from django.db.models.signals import (
    post_save, post_delete, m2m_changed, pre_delete
)
from django.dispatch import receiver
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.functional import LazyObject
from django.contrib.auth import BACKEND_SESSION_KEY
from django.utils.translation import ugettext_lazy as _
from rest_framework.renderers import JSONRenderer
from rest_framework.request import Request

from assets.models import Asset, SystemUser
from common.const.signals import POST_ADD, POST_REMOVE, POST_CLEAR
from jumpserver.utils import current_request
from common.utils import get_request_ip, get_logger, get_syslogger
from users.models import User
from users.signals import post_user_change_password
from authentication.signals import post_auth_failed, post_auth_success
from terminal.models import Session, Command
from common.utils.encode import model_to_json
from .utils import write_login_log
from . import models
from .models import OperateLog
from orgs.utils import current_org
from perms.models import AssetPermission, ApplicationPermission

logger = get_logger(__name__)
sys_logger = get_syslogger(__name__)
json_render = JSONRenderer()


MODELS_NEED_RECORD = (
    # users
    'User', 'UserGroup',
    # acls
    'LoginACL', 'LoginAssetACL', 'LoginConfirmSetting',
    # assets
    'Asset', 'Node', 'AdminUser', 'SystemUser', 'Domain', 'Gateway', 'CommandFilterRule',
    'CommandFilter', 'Platform', 'AuthBook',
    # applications
    'Application',
    # orgs
    'Organization',
    # settings
    'Setting',
    # perms
    'AssetPermission', 'ApplicationPermission',
    # xpack
    'License', 'Account', 'SyncInstanceTask', 'ChangeAuthPlan', 'GatherUserTask',
)


class AuthBackendLabelMapping(LazyObject):
    @staticmethod
    def get_login_backends():
        backend_label_mapping = {}
        for source, backends in User.SOURCE_BACKEND_MAPPING.items():
            for backend in backends:
                backend_label_mapping[backend] = source.label
        backend_label_mapping[settings.AUTH_BACKEND_PUBKEY] = _('SSH Key')
        backend_label_mapping[settings.AUTH_BACKEND_MODEL] = _('Password')
        backend_label_mapping[settings.AUTH_BACKEND_SSO] = _('SSO')
        backend_label_mapping[settings.AUTH_BACKEND_AUTH_TOKEN] = _('Auth Token')
        backend_label_mapping[settings.AUTH_BACKEND_WECOM] = _('WeCom')
        backend_label_mapping[settings.AUTH_BACKEND_DINGTALK] = _('DingTalk')
        return backend_label_mapping

    def _setup(self):
        self._wrapped = self.get_login_backends()


AUTH_BACKEND_LABEL_MAPPING = AuthBackendLabelMapping()


def create_operate_log(action, sender, resource):
    user = current_request.user if current_request else None
    if not user or not user.is_authenticated:
        return
    model_name = sender._meta.object_name
    if model_name not in MODELS_NEED_RECORD:
        return
    resource_type = sender._meta.verbose_name
    remote_addr = get_request_ip(current_request)

    data = {
        "user": str(user), 'action': action, 'resource_type': resource_type,
        'resource': str(resource), 'remote_addr': remote_addr,
    }
    with transaction.atomic():
        try:
            models.OperateLog.objects.create(**data)
        except Exception as e:
            logger.error("Create operate log error: {}".format(e))


M2M_NEED_RECORD = {
    'OrganizationMember': (
        _('User and Organization'),
        _('{User} JOINED {Organization}'),
        _('{User} LEFT {Organization}')
    ),
    User.groups.through._meta.object_name: (
        _('User and Group'),
        _('{User} JOINED {UserGroup}'),
        _('{User} LEFT {UserGroup}')
    ),
    SystemUser.assets.through._meta.object_name: (
        _('Asset and SystemUser'),
        _('{Asset} ADD {SystemUser}'),
        _('{Asset} REMOVE {SystemUser}')
    ),
    Asset.nodes.through._meta.object_name: (
        _('Node and Asset'),
        _('{Node} ADD {Asset}'),
        _('{Node} REMOVE {Asset}')
    ),
    AssetPermission.users.through._meta.object_name: (
        _('User asset permissions'),
        _('{AssetPermission} ADD {User}'),
        _('{AssetPermission} REMOVE {User}'),
    ),
    AssetPermission.user_groups.through._meta.object_name: (
        _('User group asset permissions'),
        _('{AssetPermission} ADD {UserGroup}'),
        _('{AssetPermission} REMOVE {UserGroup}'),
    ),
    AssetPermission.assets.through._meta.object_name: (
        _('Asset permission'),
        _('{AssetPermission} ADD {Asset}'),
        _('{AssetPermission} REMOVE {Asset}'),
    ),
    AssetPermission.nodes.through._meta.object_name: (
        _('Node permission'),
        _('{AssetPermission} ADD {Node}'),
        _('{AssetPermission} REMOVE {Node}'),
    ),
    AssetPermission.system_users.through._meta.object_name: (
        _('Asset permission and SystemUser'),
        _('{AssetPermission} ADD {SystemUser}'),
        _('{AssetPermission} REMOVE {SystemUser}'),
    ),
    ApplicationPermission.users.through._meta.object_name: (
        _('User application permissions'),
        _('{ApplicationPermission} ADD {User}'),
        _('{ApplicationPermission} REMOVE {User}'),
    ),
    ApplicationPermission.user_groups.through._meta.object_name: (
        _('User group application permissions'),
        _('{ApplicationPermission} ADD {UserGroup}'),
        _('{ApplicationPermission} REMOVE {UserGroup}'),
    ),
    ApplicationPermission.applications.through._meta.object_name: (
        _('Application permission'),
        _('{ApplicationPermission} ADD {Application}'),
        _('{ApplicationPermission} REMOVE {Application}'),
    ),
    ApplicationPermission.system_users.through._meta.object_name: (
        _('Application permission and SystemUser'),
        _('{ApplicationPermission} ADD {SystemUser}'),
        _('{ApplicationPermission} REMOVE {SystemUser}'),
    ),
}


M2M_ACTION = {
    POST_ADD: 'add',
    POST_REMOVE: 'remove',
    POST_CLEAR: 'remove',
}


@receiver(m2m_changed)
def on_m2m_changed(sender, action, instance, reverse, model, pk_set, **kwargs):
    if action not in M2M_ACTION:
        return

    user = current_request.user if current_request else None
    if not user or not user.is_authenticated:
        return

    sender_name = sender._meta.object_name
    if sender_name in M2M_NEED_RECORD:
        action = M2M_ACTION[action]
        org_id = current_org.id
        remote_addr = get_request_ip(current_request)
        user = str(user)
        resource_type, resource_tmpl_add, resource_tmpl_remove = M2M_NEED_RECORD[sender_name]
        if action == 'add':
            resource_tmpl = resource_tmpl_add
        elif action == 'remove':
            resource_tmpl = resource_tmpl_remove

        to_create = []
        objs = model.objects.filter(pk__in=pk_set)

        instance_name = instance._meta.object_name
        instance_value = str(instance)

        model_name = model._meta.object_name

        for obj in objs:
            resource = resource_tmpl.format(**{
                instance_name: instance_value,
                model_name: str(obj)
            })[:128]  # `resource` 字段只有 128 个字符长 😔

            to_create.append(OperateLog(
                user=user, action=action, resource_type=resource_type,
                resource=resource, remote_addr=remote_addr, org_id=org_id
            ))
        OperateLog.objects.bulk_create(to_create)


@receiver(post_save)
def on_object_created_or_update(sender, instance=None, created=False, update_fields=None, **kwargs):
    # last_login 改变是最后登录日期, 每次登录都会改变
    if instance._meta.object_name == 'User' and \
            update_fields and 'last_login' in update_fields:
        return
    if created:
        action = models.OperateLog.ACTION_CREATE
    else:
        action = models.OperateLog.ACTION_UPDATE
    create_operate_log(action, sender, instance)


@receiver(pre_delete)
def on_object_delete(sender, instance=None, **kwargs):
    create_operate_log(models.OperateLog.ACTION_DELETE, sender, instance)


@receiver(post_user_change_password, sender=User)
def on_user_change_password(sender, user=None, **kwargs):
    if not current_request:
        remote_addr = '127.0.0.1'
        change_by = 'System'
    else:
        remote_addr = get_request_ip(current_request)
        if not current_request.user.is_authenticated:
            change_by = str(user)
        else:
            change_by = str(current_request.user)
    with transaction.atomic():
        models.PasswordChangeLog.objects.create(
            user=str(user), change_by=change_by,
            remote_addr=remote_addr,
        )


def on_audits_log_create(sender, instance=None, **kwargs):
    if sender == models.UserLoginLog:
        category = "login_log"
    elif sender == models.FTPLog:
        category = "ftp_log"
    elif sender == models.OperateLog:
        category = "operation_log"
    elif sender == models.PasswordChangeLog:
        category = "password_change_log"
    elif sender == Session:
        category = "host_session_log"
    elif sender == Command:
        category = "session_command_log"
    else:
        return

    data = model_to_json(instance, indent=None)
    msg = "{} - {}".format(category, data)
    sys_logger.info(msg)


def get_login_backend(request):
    backend = request.session.get('auth_backend', '') or \
              request.session.get(BACKEND_SESSION_KEY, '')

    backend_label = AUTH_BACKEND_LABEL_MAPPING.get(backend, None)
    if backend_label is None:
        backend_label = ''
    return backend_label


def generate_data(username, request, login_type=None):
    user_agent = request.META.get('HTTP_USER_AGENT', '')
    login_ip = get_request_ip(request) or '0.0.0.0'

    if login_type is None and isinstance(request, Request):
        login_type = request.META.get('HTTP_X_JMS_LOGIN_TYPE', 'U')
    if login_type is None:
        login_type = 'W'

    data = {
        'username': username,
        'ip': login_ip,
        'type': login_type,
        'user_agent': user_agent[0:254],
        'datetime': timezone.now(),
        'backend': get_login_backend(request)
    }
    return data


@receiver(post_auth_success)
def on_user_auth_success(sender, user, request, login_type=None, **kwargs):
    logger.debug('User login success: {}'.format(user.username))
    data = generate_data(user.username, request, login_type=login_type)
    data.update({'mfa': int(user.mfa_enabled), 'status': True})
    write_login_log(**data)


@receiver(post_auth_failed)
def on_user_auth_failed(sender, username, request, reason='', **kwargs):
    logger.debug('User login failed: {}'.format(username))
    data = generate_data(username, request)
    data.update({'reason': reason[:128], 'status': False})
    write_login_log(**data)
