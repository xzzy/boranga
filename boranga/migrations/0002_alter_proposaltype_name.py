# Generated by Django 3.2.12 on 2022-03-14 23:36

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('boranga', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='proposaltype',
            name='name',
            field=models.CharField(choices=[], default='T Class', max_length=64, verbose_name='Application name (eg. T Class, Filming, Event, E Class)'),
        ),
    ]