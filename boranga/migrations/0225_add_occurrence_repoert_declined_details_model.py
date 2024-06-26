# Generated by Django 3.2.25 on 2024-04-19 02:37

import django.core.files.storage
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('boranga', '0224_auto_20240418_1430'),
    ]

    operations = [
        migrations.CreateModel(
            name='OccurrenceReportDeclinedDetails',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('officer', models.IntegerField()),
                ('reason', models.TextField(blank=True)),
                ('cc_email', models.TextField(null=True)),
                ('occurrence_report', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, to='boranga.occurrencereport')),
            ],
        ),
    ]
