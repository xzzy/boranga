# Generated by Django 3.2.12 on 2022-05-12 07:43

import boranga.components.species_and_communities.models
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('boranga', '0002_alter_proposaltype_name'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='speciesdocument',
            name='date_time',
        ),
        migrations.RemoveField(
            model_name='speciesdocument',
            name='document',
        ),
        migrations.RemoveField(
            model_name='speciesdocument',
            name='document_description',
        ),
        migrations.AddField(
            model_name='speciesdocument',
            name='_file',
            field=models.FileField(default='None', max_length=512, upload_to=boranga.components.species_and_communities.models.update_species_doc_filename),
        ),
        migrations.AddField(
            model_name='speciesdocument',
            name='can_delete',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='speciesdocument',
            name='description',
            field=models.TextField(blank=True, verbose_name='description'),
        ),
        migrations.AddField(
            model_name='speciesdocument',
            name='input_name',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name='speciesdocument',
            name='name',
            field=models.CharField(blank=True, max_length=255, verbose_name='name'),
        ),
        migrations.AddField(
            model_name='speciesdocument',
            name='uploaded_date',
            field=models.DateTimeField(auto_now_add=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='speciesdocument',
            name='visible',
            field=models.BooleanField(default=True),
        ),
        migrations.AlterField(
            model_name='speciesdocument',
            name='species',
            field=models.ForeignKey(default=None, on_delete=django.db.models.deletion.CASCADE, related_name='species_documents', to='boranga.species'),
        ),
    ]