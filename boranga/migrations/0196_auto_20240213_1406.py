# Generated by Django 3.2.23 on 2024-02-13 06:06

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('boranga', '0195_auto_20240213_1039'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='amendmentrequest',
            name='proposalrequest_ptr',
        ),
        migrations.RemoveField(
            model_name='amendmentrequest',
            name='reason',
        ),
        migrations.RemoveField(
            model_name='applicationfeediscount',
            name='proposal',
        ),
        migrations.RemoveField(
            model_name='assessment',
            name='proposalrequest_ptr',
        ),
        migrations.RemoveField(
            model_name='checklistquestion',
            name='application_type',
        ),
        migrations.RemoveField(
            model_name='compliancerequest',
            name='proposalrequest_ptr',
        ),
        migrations.RemoveField(
            model_name='onholddocument',
            name='proposal',
        ),
        migrations.RemoveField(
            model_name='proposal',
            name='application_type',
        ),
        migrations.RemoveField(
            model_name='proposal',
            name='approval_level_document',
        ),
        migrations.RemoveField(
            model_name='proposal',
            name='org_applicant',
        ),
        migrations.RemoveField(
            model_name='proposal',
            name='previous_application',
        ),
        migrations.DeleteModel(
            name='ProposalApplicantDetails',
        ),
        migrations.DeleteModel(
            name='ProposalApproverGroup',
        ),
        migrations.AlterUniqueTogether(
            name='proposalassessment',
            unique_together=None,
        ),
        migrations.RemoveField(
            model_name='proposalassessment',
            name='proposal',
        ),
        migrations.RemoveField(
            model_name='proposalassessment',
            name='referral',
        ),
        migrations.RemoveField(
            model_name='proposalassessment',
            name='referral_group',
        ),
        migrations.RemoveField(
            model_name='proposalassessmentanswer',
            name='assessment',
        ),
        migrations.RemoveField(
            model_name='proposalassessmentanswer',
            name='question',
        ),
        migrations.DeleteModel(
            name='ProposalAssessorGroup',
        ),
        migrations.RemoveField(
            model_name='proposaldeclineddetails',
            name='proposal',
        ),
        migrations.RemoveField(
            model_name='proposaldocument',
            name='proposal',
        ),
        migrations.RemoveField(
            model_name='proposallogdocument',
            name='log_entry',
        ),
        migrations.RemoveField(
            model_name='proposallogentry',
            name='communicationslogentry_ptr',
        ),
        migrations.RemoveField(
            model_name='proposallogentry',
            name='proposal',
        ),
        migrations.RemoveField(
            model_name='proposalonhold',
            name='documents',
        ),
        migrations.RemoveField(
            model_name='proposalonhold',
            name='proposal',
        ),
        migrations.RemoveField(
            model_name='proposalotherdetails',
            name='proposal',
        ),
        migrations.RemoveField(
            model_name='proposalrequest',
            name='proposal',
        ),
        migrations.RemoveField(
            model_name='proposalrequireddocument',
            name='proposal',
        ),
        migrations.RemoveField(
            model_name='proposalrequirement',
            name='copied_from',
        ),
        migrations.RemoveField(
            model_name='proposalrequirement',
            name='proposal',
        ),
        migrations.RemoveField(
            model_name='proposalrequirement',
            name='referral_group',
        ),
        migrations.RemoveField(
            model_name='proposalrequirement',
            name='standard_requirement',
        ),
        migrations.RemoveField(
            model_name='proposalstandardrequirement',
            name='application_type',
        ),
        migrations.AlterUniqueTogether(
            name='proposaltype',
            unique_together=None,
        ),
        migrations.RemoveField(
            model_name='proposaltype',
            name='replaced_by',
        ),
        migrations.RemoveField(
            model_name='proposaluseraction',
            name='proposal',
        ),
        migrations.RemoveField(
            model_name='qaofficerdocument',
            name='proposal',
        ),
        migrations.RemoveField(
            model_name='qaofficerreferral',
            name='document',
        ),
        migrations.RemoveField(
            model_name='qaofficerreferral',
            name='proposal',
        ),
        migrations.RemoveField(
            model_name='qaofficerreferral',
            name='qaofficer_group',
        ),
        migrations.RemoveField(
            model_name='referral',
            name='document',
        ),
        migrations.RemoveField(
            model_name='referral',
            name='proposal',
        ),
        migrations.RemoveField(
            model_name='referral',
            name='referral_group',
        ),
        migrations.RemoveField(
            model_name='referraldocument',
            name='referral',
        ),
        migrations.RemoveField(
            model_name='requirementdocument',
            name='requirement',
        ),
        migrations.DeleteModel(
            name='AmendmentReason',
        ),
        migrations.DeleteModel(
            name='AmendmentRequest',
        ),
        migrations.DeleteModel(
            name='ApplicationFeeDiscount',
        ),
        migrations.DeleteModel(
            name='Assessment',
        ),
        migrations.DeleteModel(
            name='ChecklistQuestion',
        ),
        migrations.DeleteModel(
            name='ComplianceRequest',
        ),
        migrations.DeleteModel(
            name='OnHoldDocument',
        ),
        migrations.DeleteModel(
            name='Proposal',
        ),
        migrations.DeleteModel(
            name='ProposalAssessment',
        ),
        migrations.DeleteModel(
            name='ProposalAssessmentAnswer',
        ),
        migrations.DeleteModel(
            name='ProposalDeclinedDetails',
        ),
        migrations.DeleteModel(
            name='ProposalDocument',
        ),
        migrations.DeleteModel(
            name='ProposalLogDocument',
        ),
        migrations.DeleteModel(
            name='ProposalLogEntry',
        ),
        migrations.DeleteModel(
            name='ProposalOnHold',
        ),
        migrations.DeleteModel(
            name='ProposalOtherDetails',
        ),
        migrations.DeleteModel(
            name='ProposalRequest',
        ),
        migrations.DeleteModel(
            name='ProposalRequiredDocument',
        ),
        migrations.DeleteModel(
            name='ProposalRequirement',
        ),
        migrations.DeleteModel(
            name='ProposalStandardRequirement',
        ),
        migrations.DeleteModel(
            name='ProposalType',
        ),
        migrations.DeleteModel(
            name='ProposalUserAction',
        ),
        migrations.DeleteModel(
            name='QAOfficerDocument',
        ),
        migrations.DeleteModel(
            name='QAOfficerGroup',
        ),
        migrations.DeleteModel(
            name='QAOfficerReferral',
        ),
        migrations.DeleteModel(
            name='Referral',
        ),
        migrations.DeleteModel(
            name='ReferralDocument',
        ),
        migrations.DeleteModel(
            name='ReferralRecipientGroup',
        ),
        migrations.DeleteModel(
            name='RequirementDocument',
        ),
    ]