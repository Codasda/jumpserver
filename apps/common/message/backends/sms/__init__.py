from collections import OrderedDict
import importlib

from django.utils.translation import gettext_lazy as _
from django.db.models import TextChoices
from django.conf import settings

from common.utils import get_logger
from common.exceptions import JMSException

logger = get_logger(__file__)


class BACKENDS(TextChoices):
    ALIBABA = 'alibaba', _('Alibaba cloud')
    TENCENT = 'tencent', _('Tencent cloud')


class BaseSMSClient:
    """
    短信终端的基类
    """

    SIGN_AND_TMPL_SETTING_FIELD_PREFIX: str

    @classmethod
    def new_from_settings(cls):
        raise NotImplementedError

    def send_sms(self, phone_numbers: list, sign_name: str, template_code: str, template_param: dict, **kwargs):
        raise NotImplementedError


class SMS:
    client: BaseSMSClient

    def __init__(self, backend=None):
        backend = backend or settings.SMS_BACKEND
        if backend not in BACKENDS:
            raise JMSException(
                code='sms_provider_not_support',
                detail=_('SMS provider not support: {}').format(backend)
            )
        m = importlib.import_module(f'.{backend or settings.SMS_BACKEND}', __package__)
        self.client = m.client.new_from_settings()

    def send_sms(self, phone_numbers: list, sign_name: str, template_code: str, template_param: dict, **kwargs):
        return self.client.send_sms(
            phone_numbers=phone_numbers,
            sign_name=sign_name,
            template_code=template_code,
            template_param=template_param,
            **kwargs
        )

    def send_verify_code(self, phone_number, code):
        sign_name = getattr(settings, f'{self.client.SIGN_AND_TMPL_SETTING_FIELD_PREFIX}_VERIFY_SIGN_NAME')
        template_code = getattr(settings, f'{self.client.SIGN_AND_TMPL_SETTING_FIELD_PREFIX}_VERIFY_TEMPLATE_CODE')

        if not (sign_name and template_code):
            raise JMSException(
                code='verify_code_sign_tmpl_invalid',
                detail=_('SMS verification code signature or template invalid')
            )
        return self.send_sms([phone_number], sign_name, template_code, OrderedDict(code=code))
