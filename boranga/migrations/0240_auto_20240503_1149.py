# Generated by Django 3.2.25 on 2024-05-03 03:49

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("boranga", "0239_auto_20240501_1347"),
    ]

    operations = [
        migrations.RenameField(
            model_name="occurrencereportreferral",
            old_name="ocurrence_report",
            new_name="occurrence_report",
        ),
    ]