# Generated by Django 3.2.25 on 2024-05-13 06:12

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("boranga", "0244_auto_20240513_1347"),
    ]

    operations = [
        migrations.DeleteModel(
            name="Profile",
        ),
        migrations.AlterModelOptions(
            name="usercategory",
            options={"verbose_name_plural": "User Categories"},
        ),
    ]
