# Generated by Django 2.2.10 on 2020-07-11 09:40

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('assets', '0049_systemuser_sftp_root'),
    ]

    operations = [
        migrations.AlterField(
            model_name='asset',
            name='created_by',
            field=models.CharField(blank=True, max_length=128, null=True, verbose_name='Created by'),
        ),
    ]