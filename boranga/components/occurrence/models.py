import json
import logging

import reversion
from django.conf import settings
from django.contrib.gis.db import models as gis_models
from django.contrib.gis.db.models.functions import Area
from django.contrib.gis.geos import GEOSGeometry, Polygon
from django.core.exceptions import ValidationError
from django.core.files.storage import FileSystemStorage
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.db.models import Count
from django.db.models.functions import Cast
from ledger_api_client.ledger_models import EmailUserRO as EmailUser
from ledger_api_client.managed_models import SystemGroup
from multiselectfield import MultiSelectField

from boranga import exceptions
from boranga.components.conservation_status.models import ProposalAmendmentReason
from boranga.components.main.models import (
    CommunicationsLogEntry,
    Document,
    RevisionedMixin,
    UserAction,
)
from boranga.components.occurrence.email import (
    send_approve_email_notification,
    send_approver_approve_email_notification,
    send_approver_back_to_assessor_email_notification,
    send_approver_decline_email_notification,
    send_decline_email_notification,
    send_occurrence_report_amendment_email_notification,
    send_occurrence_report_referral_complete_email_notification,
    send_occurrence_report_referral_email_notification,
    send_occurrence_report_referral_recall_email_notification,
)
from boranga.components.species_and_communities.models import (
    Community,
    CurrentImpact,
    DocumentCategory,
    DocumentSubCategory,
    GroupType,
    PotentialImpact,
    PotentialThreatOnset,
    Species,
    ThreatAgent,
    ThreatCategory,
)
from boranga.helpers import clone_model, email_in_dept_domains
from boranga.ledger_api_utils import retrieve_email_user
from boranga.settings import (
    GROUP_NAME_OCCURRENCE_APPROVER,
    GROUP_NAME_OCCURRENCE_ASSESSOR,
)

logger = logging.getLogger(__name__)

private_storage = FileSystemStorage(
    location=settings.BASE_DIR + "/private-media/", base_url="/private-media/"
)


def update_occurrence_report_comms_log_filename(instance, filename):
    return (
        f"{settings.MEDIA_APP_DIR}/occurrence_report/"
        f"{instance.log_entry.occurrence_report.id}/communications/{filename}"
    )


def update_occurrence_report_doc_filename(instance, filename):
    return f"{settings.MEDIA_APP_DIR}/occurrence_report/{instance.occurrence_report.id}/documents/{filename}"


def update_occurrence_doc_filename(instance, filename):
    return f"{settings.MEDIA_APP_DIR}/occurrence/{instance.occurrence.id}/documents/{filename}"


class OccurrenceReport(RevisionedMixin):
    """
    Occurrence Report for any particular species or community

    Used by:
    - Occurrence
    """

    CUSTOMER_STATUS_DRAFT = "draft"
    CUSTOMER_STATUS_WITH_ASSESSOR = "with_assessor"
    CUSTOMER_STATUS_WITH_APPROVER = "with_approver"
    CUSTOMER_STATUS_AMENDMENT_REQUIRED = "amendment_required"
    CUSTOMER_STATUS_APPROVED = "approved"
    CUSTOMER_STATUS_DECLINED = "declined"
    CUSTOMER_STATUS_DISCARDED = "discarded"
    CUSTOMER_STATUS_CLOSED = "closed"
    CUSTOMER_STATUS_PARTIALLY_APPROVED = "partially_approved"
    CUSTOMER_STATUS_PARTIALLY_DECLINED = "partially_declined"
    CUSTOMER_STATUS_CHOICES = (
        (CUSTOMER_STATUS_DRAFT, "Draft"),
        (CUSTOMER_STATUS_WITH_ASSESSOR, "Under Review"),
        (CUSTOMER_STATUS_WITH_APPROVER, "Under Review"),
        (CUSTOMER_STATUS_AMENDMENT_REQUIRED, "Amendment Required"),
        (CUSTOMER_STATUS_APPROVED, "Approved"),
        (CUSTOMER_STATUS_DECLINED, "Declined"),
        (CUSTOMER_STATUS_DISCARDED, "Discarded"),
        (CUSTOMER_STATUS_CLOSED, "DeListed"),
        (CUSTOMER_STATUS_PARTIALLY_APPROVED, "Partially Approved"),
        (CUSTOMER_STATUS_PARTIALLY_DECLINED, "Partially Declined"),
    )

    # List of statuses from above that allow a customer to edit an occurrence report.
    CUSTOMER_EDITABLE_STATE = [
        "draft",
        "amendment_required",
    ]

    # List of statuses from above that allow a customer to view an occurrence report (read-only)
    CUSTOMER_VIEWABLE_STATE = [
        "with_assessor",
        "with_approver",
        "under_review",
        "approved",
        "declined",
        "closed",
        "partially_approved",
        "partially_declined",
    ]

    PROCESSING_STATUS_TEMP = "temp"
    PROCESSING_STATUS_DRAFT = "draft"
    PROCESSING_STATUS_WITH_ASSESSOR = "with_assessor"
    PROCESSING_STATUS_WITH_REFERRAL = "with_referral"
    PROCESSING_STATUS_WITH_APPROVER = "with_approver"
    PROCESSING_STATUS_AWAITING_APPLICANT_RESPONSE = "awaiting_applicant_respone"
    PROCESSING_STATUS_AWAITING_ASSESSOR_RESPONSE = "awaiting_assessor_response"
    PROCESSING_STATUS_AWAITING_RESPONSES = "awaiting_responses"
    PROCESSING_STATUS_APPROVED = "approved"
    PROCESSING_STATUS_DECLINED = "declined"
    PROCESSING_STATUS_DISCARDED = "discarded"
    PROCESSING_STATUS_CLOSED = "closed"
    PROCESSING_STATUS_PARTIALLY_APPROVED = "partially_approved"
    PROCESSING_STATUS_PARTIALLY_DECLINED = "partially_declined"
    PROCESSING_STATUS_CHOICES = (
        (PROCESSING_STATUS_DRAFT, "Draft"),
        (PROCESSING_STATUS_WITH_ASSESSOR, "With Assessor"),
        (PROCESSING_STATUS_WITH_REFERRAL, "With Referral"),
        (PROCESSING_STATUS_WITH_APPROVER, "With Approver"),
        (PROCESSING_STATUS_AWAITING_APPLICANT_RESPONSE, "Awaiting Applicant Response"),
        (PROCESSING_STATUS_AWAITING_ASSESSOR_RESPONSE, "Awaiting Assessor Response"),
        (PROCESSING_STATUS_AWAITING_RESPONSES, "Awaiting Responses"),
        (PROCESSING_STATUS_APPROVED, "Approved"),
        (PROCESSING_STATUS_DECLINED, "Declined"),
        (PROCESSING_STATUS_DISCARDED, "Discarded"),
        (PROCESSING_STATUS_CLOSED, "DeListed"),
        (PROCESSING_STATUS_PARTIALLY_APPROVED, "Partially Approved"),
        (PROCESSING_STATUS_PARTIALLY_DECLINED, "Partially Declined"),
    )

    FINALISED_STATUSES = [
        PROCESSING_STATUS_APPROVED,
        PROCESSING_STATUS_DECLINED,
        PROCESSING_STATUS_DISCARDED,
        PROCESSING_STATUS_CLOSED,
    ]

    REVIEW_STATUS_CHOICES = (
        ("not_reviewed", "Not Reviewed"),
        ("awaiting_amendments", "Awaiting Amendments"),
        ("amended", "Amended"),
        ("accepted", "Accepted"),
    )
    customer_status = models.CharField(
        "Customer Status",
        max_length=40,
        choices=CUSTOMER_STATUS_CHOICES,
        default=CUSTOMER_STATUS_CHOICES[0][0],
    )

    APPLICATION_TYPE_CHOICES = (
        ("new_proposal", "New Application"),
        ("amendment", "Amendment"),
        ("renewal", "Renewal"),
        ("external", "External"),
    )

    RELATED_ITEM_CHOICES = [
        ("species", "Species"),
        ("community", "Community"),
        ("agendaitem", "Meeting Agenda Item"),
    ]

    # group_type of report
    group_type = models.ForeignKey(
        GroupType, on_delete=models.SET_NULL, blank=True, null=True
    )
    #
    proposal_type = models.CharField(
        "Application Status Type",
        max_length=40,
        choices=APPLICATION_TYPE_CHOICES,
        default=APPLICATION_TYPE_CHOICES[0][0],
    )

    # species related occurrence
    species = models.ForeignKey(
        Species,
        on_delete=models.CASCADE,
        related_name="occurrence_report",
        null=True,
        blank=True,
    )

    # communties related occurrence
    community = models.ForeignKey(
        Community,
        on_delete=models.CASCADE,
        related_name="occurrence_report",
        null=True,
        blank=True,
    )

    occurrence = models.ForeignKey(
        "Occurrence",
        on_delete=models.PROTECT,
        related_name="occurrence_reports",
        null=True,
        blank=True,
    )

    occurrence_report_number = models.CharField(max_length=9, blank=True, default="")

    reported_date = models.DateTimeField(auto_now_add=True, null=False, blank=False)
    effective_from = models.DateTimeField(null=True, blank=True)
    effective_to = models.DateTimeField(null=True, blank=True)
    submitter = models.IntegerField(null=True)  # EmailUserRO
    lodgement_date = models.DateTimeField(
        blank=True, null=True
    )  # TODO confirm if proposed date is the same or different

    assigned_officer = models.IntegerField(null=True)  # EmailUserRO
    assigned_approver = models.IntegerField(null=True)  # EmailUserRO
    approved_by = models.IntegerField(null=True)  # EmailUserRO
    # internal user who edits the approved conservation status(only specific fields)
    # modified_by = models.IntegerField(null=True) #EmailUserRO
    processing_status = models.CharField(
        "Processing Status",
        max_length=30,
        choices=PROCESSING_STATUS_CHOICES,
        default=PROCESSING_STATUS_CHOICES[0][0],
    )
    prev_processing_status = models.CharField(max_length=30, blank=True, null=True)

    review_due_date = models.DateField(null=True, blank=True)
    review_date = models.DateField(null=True, blank=True)
    reviewed_by = models.IntegerField(null=True)  # EmailUserRO
    review_status = models.CharField(
        "Review Status",
        max_length=30,
        choices=REVIEW_STATUS_CHOICES,
        default=REVIEW_STATUS_CHOICES[0][0],
    )

    proposed_decline_status = models.BooleanField(default=False)
    deficiency_data = models.TextField(null=True, blank=True)  # deficiency comment
    assessor_data = models.TextField(null=True, blank=True)  # assessor comment
    approver_comment = models.TextField(blank=True)
    internal_application = models.BooleanField(default=False)

    class Meta:
        app_label = "boranga"

    def __str__(self):
        return str(self.occurrence_report_number)  # TODO: is the most appropriate?

    def save(self, *args, **kwargs):
        if self.occurrence_report_number == "":
            force_insert = kwargs.pop("force_insert", False)
            super().save(no_revision=True, force_insert=force_insert)
            new_occurrence_report_id = f"OCR{str(self.pk)}"
            self.occurrence_report_number = new_occurrence_report_id
            self.save(*args, **kwargs)
        else:
            super().save(*args, **kwargs)

    @property
    def reference(self):
        # TODO : the second parameter is lodgement.sequence no. don't know yet what for species it should be
        return f"{self.occurrence_report_number}-{self.occurrence_report_number}"

    @property
    def applicant(self):
        if self.submitter:
            email_user = retrieve_email_user(self.submitter)
            return f"{email_user.first_name} {email_user.last_name}"

    @property
    def applicant_email(self):
        if self.submitter:
            email_user = retrieve_email_user(self.submitter)
            return email_user.email

    @property
    def applicant_details(self):
        if self.submitter:
            email_user = retrieve_email_user(self.submitter)
            print(email_user)
            return f"{email_user.first_name} {email_user.last_name}"
            # Priya commented the below as gives error on UAT and Dev only on external side
            # email_user.addresses.all().first())

    @property
    def applicant_address(self):
        if self.submitter:
            email_user = retrieve_email_user(self.submitter)
            return email_user.residential_address

    @property
    def applicant_id(self):
        if self.submitter:
            email_user = retrieve_email_user(self.submitter)
            return email_user.id

    @property
    def applicant_type(self):
        if self.submitter:
            # return self.APPLICANT_TYPE_SUBMITTER
            return "SUB"

    @property
    def applicant_field(self):
        # if self.org_applicant:
        #     return 'org_applicant'
        # elif self.proxy_applicant:
        #     return 'proxy_applicant'
        # else:
        #     return 'submitter'
        return "submitter"

    def log_user_action(self, action, request):
        return OccurrenceReportUserAction.log_action(self, action, request.user.id)

    @property
    def can_user_edit(self):
        """
        :return: True if the occurrence report is in one of the editable status.
        """
        return self.customer_status in self.CUSTOMER_EDITABLE_STATE

    @property
    def can_user_view(self):
        """
        :return: True if the occurrence report is in one of the approved status.
        """
        return self.customer_status in self.CUSTOMER_VIEWABLE_STATE

    @property
    def is_discardable(self):
        """
        An occurrence report can be discarded by a customer if:
        1 - It is a draft
        2- or if the occurrence report has been pushed back to the user
        """
        return (
            self.customer_status == "draft"
            or self.processing_status == "awaiting_applicant_response"
        )

    @property
    def is_flora_application(self):
        if self.group_type.name == GroupType.GROUP_TYPE_FLORA:
            return True
        return False

    @property
    def is_fauna_application(self):
        if self.group_type.name == GroupType.GROUP_TYPE_FAUNA:
            return True
        return False

    @property
    def is_community_application(self):
        if self.group_type.name == GroupType.GROUP_TYPE_COMMUNITY:
            return True
        return False

    @property
    def finalised(self):
        return self.processing_status in self.FINALISED_STATUSES

    @property
    def allowed_assessors(self):
        group = None
        # TODO: Take application_type into account
        if self.processing_status in [
            OccurrenceReport.PROCESSING_STATUS_WITH_APPROVER,
        ]:
            group = self.get_approver_group()
        elif self.processing_status in [
            OccurrenceReport.PROCESSING_STATUS_WITH_REFERRAL,
            OccurrenceReport.PROCESSING_STATUS_WITH_ASSESSOR,
        ]:
            group = self.get_assessor_group()

        users = (
            list(
                map(
                    lambda id: retrieve_email_user(id),
                    group.get_system_group_member_ids(),
                )
            )
            if group
            else []
        )
        return users

    def has_assessor_mode(self, user):
        status_with_assessor = [
            "with_assessor",
            "with_referral",
        ]
        if self.processing_status not in status_with_assessor:
            return False
        else:
            if self.assigned_officer:
                if self.assigned_officer == user.id:
                    return (
                        user.id
                        in self.get_assessor_group().get_system_group_member_ids()
                    )
                else:
                    return False
            else:
                return False

    def has_approver_mode(self, user):
        status_with_approver = [
            "with_approver",
        ]
        if self.processing_status not in status_with_approver:
            return False
        else:
            if self.assigned_approver:
                if self.assigned_approver == user.id:
                    return (
                        user.id
                        in self.get_approver_group().get_system_group_member_ids()
                    )
                else:
                    return False
            else:
                return False

    def get_assessor_group(self):
        return SystemGroup.objects.get(name=GROUP_NAME_OCCURRENCE_ASSESSOR)

    def get_approver_group(self):
        return SystemGroup.objects.get(name=GROUP_NAME_OCCURRENCE_APPROVER)

    @property
    def assessor_recipients(self):
        logger.info("assessor_recipients")
        recipients = []
        group_ids = self.get_assessor_group().get_system_group_member_ids()
        for id in group_ids:
            logger.info(id)
            recipients.append(EmailUser.objects.get(id=id).email)
        return recipients

    @property
    def approver_recipients(self):
        logger.info("approver_recipients")
        recipients = []
        group_ids = self.get_approver_group().get_system_group_member_ids()
        for id in group_ids:
            logger.info(id)
            recipients.append(EmailUser.objects.get(id=id).email)
        return recipients

    # Check if the user is member of assessor group for the OCR Proposal
    def is_assessor(self, user):
        return user.id in self.get_assessor_group().get_system_group_member_ids()

    # Check if the user is member of assessor group for the OCR Proposal
    def is_approver(self, user):
        return user.id in self.get_assessor_group().get_system_group_member_ids()

    def can_assess(self, user):
        if self.processing_status in [
            OccurrenceReport.PROCESSING_STATUS_WITH_ASSESSOR,
            OccurrenceReport.PROCESSING_STATUS_WITH_REFERRAL,
        ]:
            return user.id in self.get_assessor_group().get_system_group_member_ids()
        elif self.processing_status == OccurrenceReport.PROCESSING_STATUS_WITH_APPROVER:
            return user.id in self.get_approver_group().get_system_group_member_ids()
        else:
            return False

    @transaction.atomic
    def assign_officer(self, request, officer):
        if not self.can_assess(request.user):
            raise exceptions.OccurrenceReportNotAuthorized()

        if not self.can_assess(officer):
            raise ValidationError(
                "The selected person is not authorised to be assigned to this proposal"
            )

        if self.processing_status == OccurrenceReport.PROCESSING_STATUS_WITH_APPROVER:
            if officer.id != self.assigned_approver:
                self.assigned_approver = officer.id
                self.save(version_user=request.user)

                # Create a log entry for the proposal
                self.log_user_action(
                    OccurrenceReportUserAction.ACTION_ASSIGN_TO_APPROVER.format(
                        self.occurrence_report_number,
                        f"{officer.get_full_name()}({officer.email})",
                    ),
                    request,
                )
        else:
            if officer.id != self.assigned_officer:
                self.assigned_officer = officer.id
                self.save(version_user=request.user)

                # Create a log entry for the proposal
                self.log_user_action(
                    OccurrenceReportUserAction.ACTION_ASSIGN_TO_ASSESSOR.format(
                        self.occurrence_report_number,
                        f"{officer.get_full_name()}({officer.email})",
                    ),
                    request,
                )

    def unassign(self, request):
        if not self.can_assess(request.user):
            raise exceptions.OccurrenceReportNotAuthorized()

        if self.processing_status == OccurrenceReport.PROCESSING_STATUS_WITH_APPROVER:
            if self.assigned_approver:
                self.assigned_approver = None
                self.save(version_user=request.user)

                # Create a log entry for the proposal
                self.log_user_action(
                    OccurrenceReportUserAction.ACTION_UNASSIGN_APPROVER.format(
                        self.occurrence_report_number
                    ),
                    request,
                )
        else:
            if self.assigned_officer:
                self.assigned_officer = None
                self.save(version_user=request.user)

                # Create a log entry for the proposal
                self.log_user_action(
                    OccurrenceReportUserAction.ACTION_UNASSIGN_ASSESSOR.format(
                        self.occurrence_report_number
                    ),
                    request,
                )

    @property
    def amendment_requests(self):
        return OccurrenceReportAmendmentRequest.objects.filter(occurrence_report=self)

    @transaction.atomic
    def propose_decline(self, request, details):
        if not self.can_assess(request.user):
            raise exceptions.OccurrenceReportNotAuthorized()

        if self.processing_status != OccurrenceReport.PROCESSING_STATUS_WITH_ASSESSOR:
            raise ValidationError(
                f"You cannot propose to decline Occurrence Report {self} as the processing status is not "
                f"{OccurrenceReport.PROCESSING_STATUS_WITH_ASSESSOR}"
            )

        reason = details.get("reason")
        OccurrenceReportDeclinedDetails.objects.update_or_create(
            occurrence_report=self,
            defaults={
                "officer": request.user.id,
                "reason": reason,
            },
        )

        self.proposed_decline_status = True
        self.approver_comment = ""
        OccurrenceReportApprovalDetails.objects.filter(occurrence_report=self).delete()
        self.processing_status = OccurrenceReport.PROCESSING_STATUS_WITH_APPROVER
        self.save(version_user=request.user)

        # Log proposal action
        self.log_user_action(
            OccurrenceReportUserAction.ACTION_PROPOSED_DECLINE.format(
                self.occurrence_report_number
            ),
            request,
        )

        send_approver_decline_email_notification(reason, request, self)

    @transaction.atomic
    def decline(self, request, details):
        if not self.can_assess(request.user):
            raise exceptions.OccurrenceReportNotAuthorized()

        if self.processing_status != OccurrenceReport.PROCESSING_STATUS_WITH_APPROVER:
            raise ValidationError(
                f"You cannot decline Occurrence Report {self} as the processing status is not "
                f"{OccurrenceReport.PROCESSING_STATUS_WITH_APPROVER}"
            )

        reason = details.get("reason")

        self.processing_status = OccurrenceReport.PROCESSING_STATUS_DECLINED
        self.customer_status = OccurrenceReport.CUSTOMER_STATUS_DECLINED
        self.save(version_user=request.user)

        # Log proposal action
        self.log_user_action(
            OccurrenceReportUserAction.ACTION_DECLINE.format(
                self.occurrence_report_number,
                reason,
            ),
            request,
        )

        send_decline_email_notification(reason, request, self)

    @transaction.atomic
    def propose_approve(self, request, validated_data):
        if not self.can_assess(request.user):
            raise exceptions.OccurrenceReportNotAuthorized()

        if self.processing_status != OccurrenceReport.PROCESSING_STATUS_WITH_ASSESSOR:
            raise ValidationError(
                f"You cannot propose to decline Occurrence Report {self} as the processing status is not "
                f"{OccurrenceReport.PROCESSING_STATUS_WITH_ASSESSOR}"
            )

        occurrence = None
        occurrence_id = validated_data.get("occurrence_id", None)
        if occurrence_id:
            try:
                occurrence = Occurrence.objects.get(id=occurrence_id)
            except Occurrence.DoesNotExist:
                raise ValidationError(
                    f"Occurrence with id {occurrence_id} does not exist"
                )

        details = validated_data.get("details", None)
        new_occurrence_name = validated_data.get("new_occurrence_name", None)
        effective_from_date = validated_data.get("effective_from_date")
        effective_to_date = validated_data.get("effective_to_date")
        OccurrenceReportApprovalDetails.objects.update_or_create(
            occurrence_report=self,
            defaults={
                "officer": request.user.id,
                "occurrence": occurrence,
                "new_occurrence_name": new_occurrence_name,
                "effective_from_date": effective_from_date,
                "effective_to_date": effective_to_date,
                "details": details,
            },
        )

        self.approver_comment = ""
        self.proposed_decline_status = False
        OccurrenceReportDeclinedDetails.objects.filter(occurrence_report=self).delete()
        self.processing_status = OccurrenceReport.PROCESSING_STATUS_WITH_APPROVER
        self.save(version_user=request.user)

        # Log proposal action
        self.log_user_action(
            OccurrenceReportUserAction.ACTION_PROPOSED_APPROVAL.format(
                self.occurrence_report_number
            ),
            request,
        )

        send_approver_approve_email_notification(request, self)

    @transaction.atomic
    def approve(self, request):
        if not self.can_assess(request.user):
            raise exceptions.OccurrenceReportNotAuthorized()

        if self.processing_status != OccurrenceReport.PROCESSING_STATUS_WITH_APPROVER:
            raise ValidationError(
                f"You cannot approve Occurrence Report {self} as the processing status is not "
                f"{OccurrenceReport.PROCESSING_STATUS_WITH_APPROVER}"
            )

        if not self.approval_details:
            raise ValidationError(
                f"Approval details are required to approve Occurrence Report {self}"
            )

        self.processing_status = OccurrenceReport.PROCESSING_STATUS_APPROVED
        self.customer_status = OccurrenceReport.CUSTOMER_STATUS_APPROVED

        if self.approval_details.occurrence:
            occurrence = self.approval_details.occurrence
        else:
            if not self.approval_details.new_occurrence_name:
                raise ValidationError(
                    "New occurrence name is required to approve Occurrence Report"
                )
            occurrence = Occurrence.clone_from_occurrence_report(self)
            occurrence.occurrence_name = self.approval_details.new_occurrence_name
            occurrence.save(version_user=request.user)

        self.occurrence = occurrence
        self.save(version_user=request.user)

        # Log proposal action
        self.log_user_action(
            OccurrenceReportUserAction.ACTION_APPROVE.format(
                self.occurrence_report_number,
                request.user.get_full_name(),
            ),
            request,
        )

        send_approve_email_notification(request, self)

    @transaction.atomic
    def back_to_assessor(self, request, validated_data):
        if (
            not self.can_assess(request.user)
            or self.processing_status
            != OccurrenceReport.PROCESSING_STATUS_WITH_APPROVER
        ):
            raise exceptions.OccurrenceReportNotAuthorized()

        self.processing_status = OccurrenceReport.PROCESSING_STATUS_WITH_ASSESSOR
        self.save(version_user=request.user)

        reason = validated_data.get("reason", "")

        # Create a log entry for the proposal
        self.log_user_action(
            OccurrenceReportUserAction.ACTION_BACK_TO_ASSESSOR.format(
                self.occurrence_report_number,
                reason,
            ),
            request,
        )

        send_approver_back_to_assessor_email_notification(request, self, reason)

    @property
    def latest_referrals(self):
        return self.referrals.all()[: settings.RECENT_REFERRAL_COUNT]

    @transaction.atomic
    def send_referral(self, request, referral_email, referral_text):
        referral_email = referral_email.lower()
        if self.processing_status not in [
            OccurrenceReport.PROCESSING_STATUS_WITH_ASSESSOR,
            OccurrenceReport.PROCESSING_STATUS_WITH_REFERRAL,
        ]:
            raise exceptions.OccurrenceReportReferralCannotBeSent()

        if (
            not self.processing_status
            == OccurrenceReport.PROCESSING_STATUS_WITH_REFERRAL
        ):
            self.processing_status = OccurrenceReport.PROCESSING_STATUS_WITH_REFERRAL
            self.save(version_user=request.user)

        referral = None

        # Check if the user is in ledger
        try:
            referee = EmailUser.objects.get(email__icontains=referral_email)
        except EmailUser.DoesNotExist:
            raise ValidationError(
                "The user you want to send the referral to does not exist in the ledger database"
            )

        # Validate if it is a deparment user
        if not email_in_dept_domains(referral_email):
            raise ValidationError(
                "The user you want to send the referral to is not a member of the department"
            )

        # Check if the referral has already been sent to this user
        if OccurrenceReportReferral.objects.filter(
            referral=referee.id, occurrence_report=self
        ).exists():
            raise ValidationError("A referral has already been sent to this user")

        # Check if the user sending the referral is a referee themselves
        sent_from = OccurrenceReportReferral.SENT_CHOICE_FROM_ASSESSOR
        if OccurrenceReportReferral.objects.filter(
            occurrence_report=self,
            referral=request.user.id,
        ).exists():
            sent_from = OccurrenceReportReferral.SENT_CHOICE_FROM_REFERRAL

        # Create Referral
        referral = OccurrenceReportReferral.objects.create(
            occurrence_report=self,
            referral=referee.id,
            sent_by=request.user.id,
            sent_from=sent_from,
            text=referral_text,
            assigned_officer=request.user.id,  # TODO should'nt use assigned officer as per das
        )

        # Create a log entry for the proposal
        self.log_user_action(
            OccurrenceReportUserAction.ACTION_SEND_REFERRAL_TO.format(
                referral.id,
                self.occurrence_report_number,
                f"{referee.get_full_name()}({referee.email})",
            ),
            request,
        )

        # send email
        send_occurrence_report_referral_email_notification(referral, request)


class OccurrenceReportDeclinedDetails(models.Model):
    occurrence_report = models.OneToOneField(
        OccurrenceReport, on_delete=models.CASCADE, related_name="declined_details"
    )
    officer = models.IntegerField()  # EmailUserRO
    reason = models.TextField(blank=True)
    cc_email = models.TextField(null=True)

    class Meta:
        app_label = "boranga"


class OccurrenceReportApprovalDetails(models.Model):
    occurrence_report = models.OneToOneField(
        OccurrenceReport, on_delete=models.CASCADE, related_name="approval_details"
    )
    occurrence = models.ForeignKey(
        "Occurrence", on_delete=models.PROTECT, null=True, blank=True
    )  # If being added to an existing occurrence
    new_occurrence_name = models.CharField(max_length=200, null=True, blank=True)
    officer = models.IntegerField()  # EmailUserRO
    effective_from_date = models.DateField(null=True, blank=True)
    effective_to_date = models.DateField(null=True, blank=True)
    details = models.TextField(blank=True)

    class Meta:
        app_label = "boranga"

    @property
    def officer_name(self):
        if not self.officer:
            return None

        return retrieve_email_user(self.officer).get_full_name()


class OccurrenceReportLogEntry(CommunicationsLogEntry):
    occurrence_report = models.ForeignKey(
        OccurrenceReport, related_name="comms_logs", on_delete=models.CASCADE
    )

    def __str__(self):
        return f"{self.reference} - {self.subject}"

    class Meta:
        app_label = "boranga"

    def save(self, **kwargs):
        # save the application reference if the reference not provided
        if not self.reference:
            self.reference = self.occurrence_report.reference
        super().save(**kwargs)


class OccurrenceReportLogDocument(Document):
    log_entry = models.ForeignKey(
        "OccurrenceReportLogEntry", related_name="documents", on_delete=models.CASCADE
    )
    _file = models.FileField(
        upload_to=update_occurrence_report_comms_log_filename,
        max_length=512,
        storage=private_storage,
    )

    class Meta:
        app_label = "boranga"


class OccurrenceReportUserAction(UserAction):
    # OccurrenceReport Proposal
    ACTION_EDIT_OCCURRENCE_REPORT = "Edit occurrence report {}"
    ACTION_LODGE_PROPOSAL = "Lodge occurrence report {}"
    ACTION_SAVE_APPLICATION = "Save occurrence report {}"
    ACTION_EDIT_APPLICATION = "Edit occurrence report {}"
    ACTION_ASSIGN_TO_ASSESSOR = "Assign occurrence report {} to {} as the assessor"
    ACTION_UNASSIGN_ASSESSOR = "Unassign assessor from occurrence report {}"
    ACTION_ASSIGN_TO_APPROVER = "Assign occurrence report {} to {} as the approver"
    ACTION_UNASSIGN_APPROVER = "Unassign approver from occurrence report {}"
    ACTION_DECLINE = "Occurrence Report {} has been declined. Reason: {}"
    ACTION_APPROVE = "Occurrence Report {} has been approved by {}"
    ACTION_CLOSE_OccurrenceReport = "De list occurrence report {}"
    ACTION_DISCARD_PROPOSAL = "Discard occurrence report {}"
    ACTION_APPROVAL_LEVEL_DOCUMENT = "Assign Approval level document {}"

    # Amendment
    ACTION_ID_REQUEST_AMENDMENTS = "Request amendments"

    # Assessors
    ACTION_SAVE_ASSESSMENT_ = "Save assessment {}"
    ACTION_CONCLUDE_ASSESSMENT_ = "Conclude assessment {}"
    ACTION_PROPOSED_READY_FOR_AGENDA = (
        "Occurrence report {} has been proposed as 'ready for agenda'"
    )
    ACTION_PROPOSED_APPROVAL = (
        "Occurrence report {} has been proposed as 'for approval'"
    )
    ACTION_PROPOSED_DECLINE = "Occurrence report {} has been proposed as 'for decline'"

    # Referrals
    ACTION_SEND_REFERRAL_TO = "Send referral {} for occurrence report {} to {}"
    ACTION_RESEND_REFERRAL_TO = "Resend referral {} for occurrence report {} to {}"
    ACTION_REMIND_REFERRAL = (
        "Send reminder for referral {} for occurrence report {} to {}"
    )
    ACTION_BACK_TO_ASSESSOR = "{} sent back to assessor. Reason: {}"
    RECALL_REFERRAL = "Referral {} for occurrence report {} has been recalled by {}"
    COMMENT_REFERRAL = "Referral {} for occurrence report {} has been commented by {}"
    CONCLUDE_REFERRAL = "Referral {} for occurrence report {} has been concluded by {}"

    # Document
    ACTION_ADD_DOCUMENT = "Document {} added for occurrence report {}"
    ACTION_UPDATE_DOCUMENT = "Document {} updated for occurrence report {}"
    ACTION_DISCARD_DOCUMENT = "Document {} discarded for occurrence report {}"
    ACTION_REINSTATE_DOCUMENT = "Document {} reinstated for occurrence report {}"

    # Threat
    ACTION_ADD_THREAT = "Threat {} added for occurrence report {}"
    ACTION_UPDATE_THREAT = "Threat {} updated for occurrence report {}"
    ACTION_DISCARD_THREAT = "Threat {} discarded for occurrence report {}"
    ACTION_REINSTATE_THREAT = "Threat {} reinstated for occurrence report {}"

    class Meta:
        app_label = "boranga"
        ordering = ("-when",)

    @classmethod
    def log_action(cls, occurrence_report, action, user):
        return cls.objects.create(
            occurrence_report=occurrence_report, who=user, what=str(action)
        )

    occurrence_report = models.ForeignKey(
        OccurrenceReport, related_name="action_logs", on_delete=models.CASCADE
    )


def update_occurrence_report_referral_doc_filename(instance, filename):
    return "{}/occurrence_report/{}/referral/{}".format(
        settings.MEDIA_APP_DIR, instance.referral.occurrence_report.id, filename
    )


class OccurrenceReportProposalRequest(models.Model):
    occurrence_report = models.ForeignKey(OccurrenceReport, on_delete=models.CASCADE)
    subject = models.CharField(max_length=200, blank=True)
    text = models.TextField(blank=True)
    officer = models.IntegerField(null=True)  # EmailUserRO

    class Meta:
        app_label = "boranga"
        ordering = ["id"]


class OccurrenceReportAmendmentRequest(OccurrenceReportProposalRequest):
    STATUS_CHOICE_REQUESTED = "requested"
    STATUS_CHOICE_AMENDED = "amended"

    STATUS_CHOICES = (
        (STATUS_CHOICE_REQUESTED, "Requested"),
        (STATUS_CHOICE_AMENDED, "Amended"),
    )

    status = models.CharField(
        "Status", max_length=30, choices=STATUS_CHOICES, default=STATUS_CHOICES[0][0]
    )
    reason = models.ForeignKey(
        ProposalAmendmentReason, blank=True, null=True, on_delete=models.SET_NULL
    )

    class Meta:
        app_label = "boranga"
        ordering = ["id"]

    @transaction.atomic
    def generate_amendment(self, request):
        if not self.occurrence_report.can_assess(request.user):
            raise exceptions.ProposalNotAuthorized()

        if self.status == "requested":
            occurrence_report = self.occurrence_report
            if occurrence_report.processing_status != "draft":
                occurrence_report.processing_status = "draft"
                occurrence_report.customer_status = "draft"
                occurrence_report.save(version_user=request.user)

            # Create a log entry for the occurrence report
            occurrence_report.log_user_action(
                OccurrenceReportUserAction.ACTION_ID_REQUEST_AMENDMENTS, request
            )

            # Create a log entry for the organisation
            # if occurrence_report.applicant:
            #     occurrence_report.applicant.log_user_action
            # (OccurrenceReportUserAction.ACTION_ID_REQUEST_AMENDMENTS, request)

            # send email
            send_occurrence_report_amendment_email_notification(
                self, request, occurrence_report
            )

        self.save()

    @transaction.atomic
    def add_documents(self, request):
        # save the files
        data = json.loads(request.data.get("data"))

        if not data.get("update"):
            documents_qs = self.amendment_request_documents.filter(
                input_name="amendment_request_doc", visible=True
            )
            documents_qs.delete()

        for idx in range(data["num_files"]):
            _file = request.data.get("file-" + str(idx))
            document = self.amendment_request_documents.create(
                _file=_file, name=_file.name
            )
            document.input_name = data["input_name"]
            document.can_delete = True
            document.save()

        # end save documents
        self.save()


def update_occurrence_report_amendment_request_doc_filename(instance, filename):
    return "occurrence_report/{}/amendment_request_documents/{}".format(
        instance.occurrence_report_amendment_request.occurrence_report.id, filename
    )


class OccurrenceReportAmendmentRequestDocument(Document):
    occurrence_report_amendment_request = models.ForeignKey(
        "OccurrenceReportAmendmentRequest",
        related_name="amendment_request_documents",
        on_delete=models.CASCADE,
    )
    _file = models.FileField(
        upload_to=update_occurrence_report_amendment_request_doc_filename,
        max_length=500,
        storage=private_storage,
    )
    input_name = models.CharField(max_length=255, null=True, blank=True)
    can_delete = models.BooleanField(
        default=True
    )  # after initial submit prevent document from being deleted
    visible = models.BooleanField(
        default=True
    )  # to prevent deletion on file system, hidden and still be available in history

    def delete(self):
        if self.can_delete:
            return super().delete()


class OccurrenceReportReferralDocument(Document):
    referral = models.ForeignKey(
        "OccurrenceReportReferral",
        related_name="referral_documents",
        on_delete=models.CASCADE,
    )
    _file = models.FileField(
        upload_to=update_occurrence_report_referral_doc_filename,
        max_length=512,
        storage=private_storage,
    )
    input_name = models.CharField(max_length=255, null=True, blank=True)
    can_delete = models.BooleanField(
        default=True
    )  # after initial submit prevent document from being deleted

    def delete(self):
        if self.can_delete:
            if self._file:
                self._file.delete()
            return super().delete()
        logger.info(
            "Cannot delete existing document object after occurrence report referral has been submitted: {}".format(
                self.name
            )
        )

    class Meta:
        app_label = "boranga"


class OccurrenceReportReferral(models.Model):
    SENT_CHOICE_FROM_ASSESSOR = 1
    SENT_CHOICE_FROM_REFERRAL = 2

    SENT_CHOICES = (
        (SENT_CHOICE_FROM_ASSESSOR, "Sent From Assessor"),
        (SENT_CHOICE_FROM_REFERRAL, "Sent From Referral"),
    )
    PROCESSING_STATUS_WITH_REFERRAL = "with_referral"
    PROCESSING_STATUS_RECALLED = "recalled"
    PROCESSING_STATUS_COMPLETED = "completed"
    PROCESSING_STATUS_CHOICES = (
        (PROCESSING_STATUS_WITH_REFERRAL, "Awaiting"),
        (PROCESSING_STATUS_RECALLED, "Recalled"),
        (PROCESSING_STATUS_COMPLETED, "Completed"),
    )
    lodged_on = models.DateTimeField(auto_now_add=True)
    occurrence_report = models.ForeignKey(
        OccurrenceReport, related_name="referrals", on_delete=models.CASCADE
    )
    sent_by = models.IntegerField()  # EmailUserRO
    referral = models.IntegerField()  # EmailUserRO
    linked = models.BooleanField(default=False)
    sent_from = models.SmallIntegerField(
        choices=SENT_CHOICES, default=SENT_CHOICES[0][0]
    )
    processing_status = models.CharField(
        "Processing Status",
        max_length=30,
        choices=PROCESSING_STATUS_CHOICES,
        default=PROCESSING_STATUS_CHOICES[0][0],
    )
    text = models.TextField(blank=True)  # Assessor text when send_referral
    referral_text = models.TextField(
        blank=True
    )  # used in other projects for complete referral comment but not used in boranga
    referral_comment = models.TextField(blank=True, null=True)  # Referral Comment
    document = models.ForeignKey(
        OccurrenceReportReferralDocument,
        blank=True,
        null=True,
        related_name="referral_document",
        on_delete=models.SET_NULL,
    )
    assigned_officer = models.IntegerField(null=True)  # EmailUserRO

    class Meta:
        app_label = "boranga"
        ordering = ("-lodged_on",)

    def __str__(self):
        return "Occurrence Report {} - Referral {}".format(
            self.occurrence_report.id, self.id
        )

    @property
    def can_be_completed(self):
        # Referral cannot be completed until second level referral sent by referral has been completed/recalled
        return not OccurrenceReportReferral.objects.filter(
            sent_by=self.referral,
            occurrence_report=self.occurrence_report,
            processing_status=OccurrenceReportReferral.PROCESSING_STATUS_WITH_REFERRAL,
        ).exists()

    def can_process(self, user):
        return True  # TODO: implement

    @property
    def referral_as_email_user(self):
        return retrieve_email_user(self.referral)

    @transaction.atomic
    def remind(self, request):
        if not self.occurrence_report.can_assess(request.user):
            raise exceptions.OccurrenceReportNotAuthorized()

        # Create a log entry for the proposal
        self.occurrence_report.log_user_action(
            OccurrenceReportUserAction.ACTION_REMIND_REFERRAL.format(
                self.id,
                self.occurrence_report.occurrence_report_number,
                f"{self.referral_as_email_user.get_full_name()}",
            ),
            request,
        )

        # Create a log entry for the submitter
        if self.occurrence_report.submitter:
            submitter = retrieve_email_user(self.occurrence_report.submitter)
            submitter.log_user_action(
                OccurrenceReportUserAction.ACTION_REMIND_REFERRAL.format(
                    self.id,
                    self.occurrence_report.occurrence_report_number,
                    f"{submitter.get_full_name()}",
                ),
                request,
            )

        # send email
        send_occurrence_report_referral_email_notification(
            self,
            request,
            reminder=True,
        )

    @transaction.atomic
    def recall(self, request):
        if not self.occurrence_report.can_assess(request.user):
            raise exceptions.OccurrenceReportNotAuthorized()

        self.processing_status = self.PROCESSING_STATUS_RECALLED
        self.save()

        send_occurrence_report_referral_recall_email_notification(self, request)

        # Create a log entry for the occurrence report
        self.occurrence_report.log_user_action(
            OccurrenceReportUserAction.RECALL_REFERRAL.format(
                self.id,
                self.occurrence_report.occurrence_report_number,
                request.user.get_full_name(),
            ),
            request,
        )

        # Create a log entry for the submitter
        if self.occurrence_report.submitter:
            submitter = retrieve_email_user(self.occurrence_report.submitter)
            submitter.log_user_action(
                OccurrenceReportUserAction.RECALL_REFERRAL.format(
                    self.id,
                    self.occurrence_report.occurrence_report_number,
                    request.user.get_full_name(),
                ),
                request,
            )

    @transaction.atomic
    def resend(self, request):
        if not self.occurrence_report.can_assess(request.user):
            raise exceptions.OccurrenceReportNotAuthorized()

        self.processing_status = self.PROCESSING_STATUS_WITH_REFERRAL
        self.occurrence_report.processing_status = self.PROCESSING_STATUS_WITH_REFERRAL
        self.occurrence_report.save()

        self.save()

        # Create a log entry for the occurrence report
        self.occurrence_report.log_user_action(
            OccurrenceReportUserAction.ACTION_RESEND_REFERRAL_TO.format(
                self.id,
                self.occurrence_report.occurrence_report_number,
                "{}({})".format(
                    self.referral_as_email_user.get_full_name(),
                    self.referral_as_email_user.email,
                ),
            ),
            request,
        )

        # Create a log entry for the submitter
        if self.occurrence_report.submitter:
            submitter = retrieve_email_user(self.occurrence_report.submitter)
            submitter.log_user_action(
                OccurrenceReportUserAction.ACTION_RESEND_REFERRAL_TO.format(
                    self.id,
                    self.occurrence_report.occurrence_report_number,
                    "{}({})".format(
                        self.referral_as_email_user.get_full_name(),
                        self.referral_as_email_user.email,
                    ),
                ),
                request,
            )

        # send email
        send_occurrence_report_referral_email_notification(self, request)

    @transaction.atomic
    def complete(self, request):
        if request.user.id != self.referral:
            raise exceptions.ReferralNotAuthorized()

        self.processing_status = self.PROCESSING_STATUS_COMPLETED
        self.save()

        outstanding = self.occurrence_report.referrals.filter(
            processing_status=self.PROCESSING_STATUS_WITH_REFERRAL
        )
        if len(outstanding) == 0:
            self.occurrence_report.processing_status = (
                OccurrenceReport.PROCESSING_STATUS_WITH_ASSESSOR
            )
            self.occurrence_report.save()

        # Create a log entry for the occurrence report
        self.occurrence_report.log_user_action(
            OccurrenceReportUserAction.CONCLUDE_REFERRAL.format(
                self.id,
                self.occurrence_report.occurrence_report_number,
                "{}({})".format(
                    self.referral_as_email_user.get_full_name(),
                    self.referral_as_email_user.email,
                ),
            ),
            request,
        )

        # Create a log entry for the submitter
        if self.occurrence_report.submitter:
            submitter = retrieve_email_user(self.occurrence_report.submitter)
            submitter.log_user_action(
                OccurrenceReportUserAction.CONCLUDE_REFERRAL.format(
                    self.id,
                    self.occurrence_report.occurrence_report_number,
                    "{}({})".format(
                        submitter.get_full_name(),
                        submitter.email,
                    ),
                ),
                request,
            )

        send_occurrence_report_referral_complete_email_notification(self, request)

    def can_assess_referral(self, user):
        return self.processing_status == self.PROCESSING_STATUS_WITH_REFERRAL

    @property
    def can_be_processed(self):
        return self.processing_status == self.PROCESSING_STATUS_WITH_REFERRAL


class Datum(models.Model):
    """
    # Admin List

    Used by:
    - Location

    """

    name = models.CharField(max_length=250, blank=False, null=False, unique=True)

    class Meta:
        app_label = "boranga"
        ordering = ["name"]

    def __str__(self):
        return str(self.name)


class CoordinationSource(models.Model):
    """
    # Admin List

    Used by:
    - Location

    """

    name = models.CharField(max_length=250, blank=False, null=False, unique=True)

    class Meta:
        app_label = "boranga"
        verbose_name = "Coordination Source"
        verbose_name_plural = "Coordination Sources"
        ordering = ["name"]

    def __str__(self):
        return str(self.name)


class LocationAccuracy(models.Model):
    """
    # Admin List

    Used by:
    - Location

    """

    name = models.CharField(max_length=250, blank=False, null=False, unique=True)

    class Meta:
        app_label = "boranga"
        verbose_name = "Location Accuracy"
        verbose_name_plural = "Location Accuracy"
        ordering = ["name"]

    def __str__(self):
        return str(self.name)


class Location(models.Model):
    """
    Location data  for occurrence report

    Used for:
    - Occurrence Report
    Is:
    - Table
    """

    occurrence_report = models.OneToOneField(
        OccurrenceReport, on_delete=models.CASCADE, null=True, related_name="location"
    )
    observation_date = models.DateTimeField(null=True, blank=True)
    location_description = models.TextField(null=True, blank=True)
    boundary_description = models.TextField(null=True, blank=True)
    new_occurrence = models.BooleanField(null=True, blank=True)
    boundary = models.IntegerField(null=True, blank=True, default=0)
    mapped_boundary = models.BooleanField(null=True, blank=True)
    buffer_radius = models.IntegerField(null=True, blank=True, default=0)
    datum = models.ForeignKey(Datum, on_delete=models.SET_NULL, null=True, blank=True)
    epsg_code = models.IntegerField(null=False, blank=False, default=4326)
    coordination_source = models.ForeignKey(
        CoordinationSource, on_delete=models.SET_NULL, null=True, blank=True
    )
    location_accuracy = models.ForeignKey(
        LocationAccuracy, on_delete=models.SET_NULL, null=True, blank=True
    )
    geojson_point = gis_models.PointField(srid=4326, blank=True, null=True)
    geojson_polygon = gis_models.PolygonField(srid=4326, blank=True, null=True)

    class Meta:
        app_label = "boranga"

    def __str__(self):
        return str(self.occurrence_report)  # TODO: is the most appropriate?


class OccurrenceReportGeometryManager(models.Manager):
    def get_queryset(self):
        qs = super().get_queryset()
        polygon_ids = qs.extra(
            where=["geometrytype(geometry) LIKE 'POLYGON'"]
        ).values_list("id", flat=True)
        return qs.annotate(
            area=models.Case(
                models.When(
                    models.Q(geometry__isnull=False) & models.Q(id__in=polygon_ids),
                    then=Area(
                        Cast("geometry", gis_models.PolygonField(geography=True))
                    ),
                ),
                default=None,
            )
        )


class OccurrenceReportGeometry(models.Model):
    objects = OccurrenceReportGeometryManager()

    EXTENT = (112.5, -35.5, 129.0, -13.5)

    occurrence_report = models.ForeignKey(
        OccurrenceReport,
        on_delete=models.CASCADE,
        null=True,
        related_name="ocr_geometry",
    )
    # Extents of WA
    geometry = gis_models.GeometryField(extent=EXTENT, blank=True, null=True)
    original_geometry_ewkb = models.BinaryField(
        blank=True, null=True, editable=True
    )  # original geometry as uploaded by the user in EWKB format (keeps the srid)
    intersects = models.BooleanField(default=False)
    copied_from = models.ForeignKey(
        "self", on_delete=models.SET_NULL, blank=True, null=True
    )
    drawn_by = models.IntegerField(blank=True, null=True)  # EmailUserRO
    locked = models.BooleanField(default=False)

    class Meta:
        app_label = "boranga"

    def __str__(self):
        return str(self.occurrence_report)  # TODO: is the most appropriate?

    def save(self, *args, **kwargs):
        if (
            self.occurrence_report.group_type.name == GroupType.GROUP_TYPE_FAUNA
            and type(self.geometry).__name__ in ["Polygon", "MultiPolygon"]
        ):
            raise ValidationError("Fauna occurrence reports cannot have polygons")

        if not self.geometry.within(
            GEOSGeometry(Polygon.from_bbox(self.EXTENT), srid=4326)
        ):
            raise ValidationError(
                "A geometry is not within the extent of Western Australia"
            )

        super().save(*args, **kwargs)

    @property
    def area_sqm(self):
        if not hasattr(self, "area") or not self.area:
            return None
        return self.area.sq_m

    @property
    def area_sqhm(self):
        if not hasattr(self, "area") or not self.area:
            return None
        return self.area.sq_m / 10000

    @property
    def original_geometry_srid(self):
        if self.original_geometry_ewkb:
            return GEOSGeometry(self.original_geometry_ewkb).srid
        return None


class OCRObserverDetail(models.Model):
    """
    Observer data  for occurrence report

    Used for:
    - Occurrence Report
    Is:
    - Table
    """

    occurrence_report = models.ForeignKey(
        OccurrenceReport,
        on_delete=models.CASCADE,
        null=True,
        related_name="observer_detail",
    )
    observer_name = models.CharField(max_length=250, blank=True, null=True)
    role = models.CharField(max_length=250, blank=True, null=True)
    contact = models.CharField(max_length=250, blank=True, null=True)
    organisation = models.CharField(max_length=250, blank=True, null=True)
    main_observer = models.BooleanField(null=True, blank=True)

    class Meta:
        app_label = "boranga"
        unique_together = (
            "observer_name",
            "occurrence_report",
        )

    def __str__(self):
        return str(self.occurrence_report)  # TODO: is the most appropriate?


# Is used in HabitatComposition for multiple selection
class LandForm(models.Model):
    """
    # Admin List

    Used by:
    - HabitatComposition

    """

    name = models.CharField(max_length=250, blank=False, null=False, unique=True)

    class Meta:
        app_label = "boranga"
        verbose_name = "Land Form"
        verbose_name_plural = "Land Forms"
        ordering = ["name"]

    def __str__(self):
        return str(self.name)


class RockType(models.Model):
    """
    # Admin List

    Used by:
    - HabitatComposition

    """

    name = models.CharField(max_length=250, blank=False, null=False, unique=True)

    class Meta:
        app_label = "boranga"
        verbose_name = "Rock Type"
        verbose_name_plural = "Rock Types"
        ordering = ["name"]

    def __str__(self):
        return str(self.name)


class SoilType(models.Model):
    """
    # Admin List

    Used by:
    - HabitatComposition

    """

    name = models.CharField(max_length=250, blank=False, null=False, unique=True)

    class Meta:
        app_label = "boranga"
        verbose_name = "Soil Type"
        verbose_name_plural = "Soil Types"
        ordering = ["name"]

    def __str__(self):
        return str(self.name)


class SoilColour(models.Model):
    """
    # Admin List

    Used by:
    - HabitatComposition

    """

    name = models.CharField(max_length=250, blank=False, null=False, unique=True)

    class Meta:
        app_label = "boranga"
        verbose_name = "Soil Colour"
        verbose_name_plural = "Soil Colours"
        ordering = ["name"]

    def __str__(self):
        return str(self.name)


class Drainage(models.Model):
    """
    # Admin List

    Used by:
    - HabitatComposition

    """

    name = models.CharField(max_length=250, blank=False, null=False, unique=True)

    class Meta:
        app_label = "boranga"
        verbose_name = "Drainage"
        verbose_name_plural = "Drainages"
        ordering = ["name"]

    def __str__(self):
        return str(self.name)


class SoilCondition(models.Model):
    """
    # Admin List

    Used by:
    - HabitatComposition

    """

    name = models.CharField(max_length=250, blank=False, null=False, unique=True)

    class Meta:
        app_label = "boranga"
        verbose_name = "Soil Condition"
        verbose_name_plural = "Soil Conditions"
        ordering = ["name"]

    def __str__(self):
        return str(self.name)


class OCRHabitatComposition(models.Model):
    """
    Habitat data  for occurrence report

    Used for:
    - Occurrence Report
    Is:
    - Table
    """

    occurrence_report = models.OneToOneField(
        OccurrenceReport,
        on_delete=models.CASCADE,
        null=True,
        related_name="habitat_composition",
    )
    land_form = MultiSelectField(max_length=250, blank=True, choices=[], null=True)
    rock_type = models.ForeignKey(
        RockType, on_delete=models.SET_NULL, null=True, blank=True
    )
    loose_rock_percent = models.IntegerField(
        null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(100)]
    )
    soil_type = models.ForeignKey(
        SoilType, on_delete=models.SET_NULL, null=True, blank=True
    )
    soil_colour = models.ForeignKey(
        SoilColour, on_delete=models.SET_NULL, null=True, blank=True
    )
    soil_condition = models.ForeignKey(
        SoilCondition, on_delete=models.SET_NULL, null=True, blank=True
    )
    drainage = models.ForeignKey(
        Drainage, on_delete=models.SET_NULL, null=True, blank=True
    )
    water_quality = models.CharField(max_length=500, null=True, blank=True)
    habitat_notes = models.CharField(max_length=1000, null=True, blank=True)

    class Meta:
        app_label = "boranga"

    def __str__(self):
        return str(self.occurrence_report)  # TODO: is the most appropriate?\


class OCRHabitatCondition(models.Model):
    """
    Habitat Condition data for occurrence report

    Used for:
    - Occurrence Report
    Is:
    - Table
    """

    occurrence_report = models.OneToOneField(
        OccurrenceReport,
        on_delete=models.CASCADE,
        null=True,
        related_name="habitat_condition",
    )
    pristine = models.IntegerField(
        null=True,
        blank=True,
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    excellent = models.IntegerField(
        null=True,
        blank=True,
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    very_good = models.IntegerField(
        null=True,
        blank=True,
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    good = models.IntegerField(
        null=True,
        blank=True,
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    degraded = models.IntegerField(
        null=True,
        blank=True,
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    completely_degraded = models.IntegerField(
        null=True,
        blank=True,
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )

    class Meta:
        app_label = "boranga"

    def __str__(self):
        return str(self.occurrence_report)


class Intensity(models.Model):
    """
    # Admin List

    Used by:
    - FireHistory

    """

    name = models.CharField(max_length=250, blank=False, null=False, unique=True)

    class Meta:
        app_label = "boranga"
        verbose_name = "Intensity"
        verbose_name_plural = "Intensities"
        ordering = ["name"]

    def __str__(self):
        return str(self.name)


class OCRFireHistory(models.Model):
    """
    Fire History data for occurrence report

    Used for:
    - Occurrence Report
    Is:
    - Table
    """

    occurrence_report = models.OneToOneField(
        OccurrenceReport,
        on_delete=models.CASCADE,
        null=True,
        related_name="fire_history",
    )
    last_fire_estimate = models.DateField(null=True, blank=True)
    intensity = models.ForeignKey(
        Intensity, on_delete=models.SET_NULL, null=True, blank=True
    )
    comment = models.CharField(max_length=1000, null=True, blank=True)

    class Meta:
        app_label = "boranga"

    def __str__(self):
        return str(self.occurrence_report)


class OCRAssociatedSpecies(models.Model):
    """
    Associated Species data for occurrence report

    Used for:
    - Occurrence Report
    Is:
    - Table
    """

    occurrence_report = models.OneToOneField(
        OccurrenceReport,
        on_delete=models.CASCADE,
        null=True,
        related_name="associated_species",
    )
    related_species = models.TextField(blank=True)

    class Meta:
        app_label = "boranga"

    def __str__(self):
        return str(self.occurrence_report)


class ObservationMethod(models.Model):
    """
    # Admin List

    Used by:
    - ObservationDetail

    """

    name = models.CharField(max_length=250, blank=False, null=False, unique=True)

    class Meta:
        app_label = "boranga"
        verbose_name = "Observation Method"
        verbose_name_plural = "Observation Methods"
        ordering = ["name"]

    def __str__(self):
        return str(self.name)


class OCRObservationDetail(models.Model):
    """
    Observation Details data for occurrence report

    Used for:
    - Occurrence Report
    Is:
    - Table
    """

    occurrence_report = models.OneToOneField(
        OccurrenceReport,
        on_delete=models.CASCADE,
        null=True,
        related_name="observation_detail",
    )
    observation_method = models.ForeignKey(
        ObservationMethod, on_delete=models.SET_NULL, null=True, blank=True
    )
    area_surveyed = models.IntegerField(null=True, blank=True, default=0)
    survey_duration = models.IntegerField(null=True, blank=True, default=0)

    class Meta:
        app_label = "boranga"

    def __str__(self):
        return str(self.occurrence_report)


class PlantCountMethod(models.Model):
    """
    # Admin List

    Used by:
    - PlantCount

    """

    name = models.CharField(max_length=250, blank=False, null=False, unique=True)

    class Meta:
        app_label = "boranga"
        verbose_name = "Plant Count Method"
        verbose_name_plural = "Plant Count Methods"
        ordering = ["name"]

    def __str__(self):
        return str(self.name)


class PlantCountAccuracy(models.Model):
    """
    # Admin List

    Used by:
    - PlantCount

    """

    name = models.CharField(max_length=250, blank=False, null=False, unique=True)

    class Meta:
        app_label = "boranga"
        verbose_name = "Plant Count Accuracy"
        verbose_name_plural = "Plant Count Accuracies"
        ordering = ["name"]

    def __str__(self):
        return str(self.name)


class CountedSubject(models.Model):
    """
    # Admin List

    Used by:
    - PlantCount

    """

    name = models.CharField(max_length=250, blank=False, null=False, unique=True)

    class Meta:
        app_label = "boranga"
        verbose_name = "Counted Subject"
        verbose_name_plural = "Counted Subjects"
        ordering = ["name"]

    def __str__(self):
        return str(self.name)


class PlantCondition(models.Model):
    """
    # Admin List

    Used by:
    - PlantCount

    """

    name = models.CharField(max_length=250, blank=False, null=False, unique=True)

    class Meta:
        app_label = "boranga"
        verbose_name = "Plant Condition"
        verbose_name_plural = "Plant Conditions"
        ordering = ["name"]

    def __str__(self):
        return str(self.name)


class OCRPlantCount(models.Model):
    """
    Plant Count data for occurrence report

    Used for:
    - Occurrence Report
    Is:
    - Table
    """

    occurrence_report = models.OneToOneField(
        OccurrenceReport,
        on_delete=models.CASCADE,
        null=True,
        related_name="plant_count",
    )
    plant_count_method = models.ForeignKey(
        PlantCountMethod, on_delete=models.SET_NULL, null=True, blank=True
    )
    plant_count_accuracy = models.ForeignKey(
        PlantCountAccuracy, on_delete=models.SET_NULL, null=True, blank=True
    )
    counted_subject = models.ForeignKey(
        CountedSubject, on_delete=models.SET_NULL, null=True, blank=True
    )
    plant_condition = models.ForeignKey(
        PlantCondition, on_delete=models.SET_NULL, null=True, blank=True
    )
    estimated_population_area = models.IntegerField(null=True, blank=True, default=0)

    detailed_alive_mature = models.IntegerField(null=True, blank=True, default=0)
    detailed_dead_mature = models.IntegerField(null=True, blank=True, default=0)
    detailed_alive_juvenile = models.IntegerField(null=True, blank=True, default=0)
    detailed_dead_juvenile = models.IntegerField(null=True, blank=True, default=0)
    detailed_alive_seedling = models.IntegerField(null=True, blank=True, default=0)
    detailed_dead_seedling = models.IntegerField(null=True, blank=True, default=0)
    detailed_alive_unknown = models.IntegerField(null=True, blank=True, default=0)
    detailed_dead_unknown = models.IntegerField(null=True, blank=True, default=0)

    simple_alive = models.IntegerField(null=True, blank=True, default=0)
    simple_dead = models.IntegerField(null=True, blank=True, default=0)

    quadrats_present = models.BooleanField(null=True, blank=True)
    quadrats_data_attached = models.BooleanField(null=True, blank=True)
    quadrats_surveyed = models.IntegerField(null=True, blank=True, default=0)
    individual_quadrat_area = models.IntegerField(null=True, blank=True, default=0)
    total_quadrat_area = models.IntegerField(null=True, blank=True, default=0)
    flowering_plants_per = models.IntegerField(
        null=True,
        blank=True,
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )

    clonal_reproduction_present = models.BooleanField(null=True, blank=True)
    vegetative_state_present = models.BooleanField(null=True, blank=True)
    flower_bud_present = models.BooleanField(null=True, blank=True)
    flower_present = models.BooleanField(null=True, blank=True)
    immature_fruit_present = models.BooleanField(null=True, blank=True)
    ripe_fruit_present = models.BooleanField(null=True, blank=True)
    dehisced_fruit_present = models.BooleanField(null=True, blank=True)
    pollinator_observation = models.CharField(max_length=1000, null=True, blank=True)
    comment = models.CharField(max_length=1000, null=True, blank=True)

    class Meta:
        app_label = "boranga"

    def __str__(self):
        return str(self.occurrence_report)


# used for Animal Observation(MultipleSelect)
class PrimaryDetectionMethod(models.Model):
    """
    # Admin List

    Used by:
    - AnimalObservation (MultipleSelect)

    """

    name = models.CharField(max_length=250, blank=False, null=False, unique=True)

    class Meta:
        app_label = "boranga"
        ordering = ["name"]

    def __str__(self):
        return str(self.name)


# used for Animal Observation(MultipleSelect)
class ReproductiveMaturity(models.Model):
    """
    # Admin List

    Used by:
    - AnimalObservation (MultipleSelect)

    """

    name = models.CharField(max_length=250, blank=False, null=False, unique=True)

    class Meta:
        app_label = "boranga"
        verbose_name = "Reproductive Maturity"
        verbose_name_plural = "Reproductive Maturities"
        ordering = ["name"]

    def __str__(self):
        return str(self.name)


class AnimalHealth(models.Model):
    """
    # Admin List

    Used by:
    - AnimalObservation

    """

    name = models.CharField(max_length=250, blank=False, null=False, unique=True)

    class Meta:
        app_label = "boranga"
        verbose_name = "Animal Health"
        verbose_name_plural = "Animal Health"
        ordering = ["name"]

    def __str__(self):
        return str(self.name)


class DeathReason(models.Model):
    """
    # Admin List

    Used by:
    - AnimalObservation

    """

    name = models.CharField(max_length=250, blank=False, null=False, unique=True)

    class Meta:
        app_label = "boranga"
        ordering = ["name"]

    def __str__(self):
        return str(self.name)


# sed for Animal Observation(MultipleSelect)
class SecondarySign(models.Model):
    """
    # Admin List

    Used by:
    - AnimalObservation (MultipleSelect)

    """

    name = models.CharField(max_length=250, blank=False, null=False, unique=True)

    class Meta:
        app_label = "boranga"
        ordering = ["name"]

    def __str__(self):
        return str(self.name)


class OCRAnimalObservation(models.Model):
    """
    Animal Observation data for occurrence report

    Used for:
    - Occurrence Report
    Is:
    - Table
    """

    occurrence_report = models.OneToOneField(
        OccurrenceReport,
        on_delete=models.CASCADE,
        null=True,
        related_name="animal_observation",
    )
    primary_detection_method = MultiSelectField(
        max_length=250, blank=True, choices=[], null=True
    )
    reproductive_maturity = MultiSelectField(
        max_length=250, blank=True, choices=[], null=True
    )
    animal_health = models.ForeignKey(
        AnimalHealth, on_delete=models.SET_NULL, null=True, blank=True
    )
    death_reason = models.ForeignKey(
        DeathReason, on_delete=models.SET_NULL, null=True, blank=True
    )
    secondary_sign = MultiSelectField(max_length=250, blank=True, choices=[], null=True)

    total_count = models.IntegerField(null=True, blank=True, default=0)
    distinctive_feature = models.CharField(max_length=1000, null=True, blank=True)
    action_taken = models.CharField(max_length=1000, null=True, blank=True)
    action_required = models.CharField(max_length=1000, null=True, blank=True)
    observation_detail_comment = models.CharField(
        max_length=1000, null=True, blank=True
    )

    alive_adult = models.IntegerField(null=True, blank=True, default=0)
    dead_adult = models.IntegerField(null=True, blank=True, default=0)
    alive_juvenile = models.IntegerField(null=True, blank=True, default=0)
    dead_juvenile = models.IntegerField(null=True, blank=True, default=0)
    alive_pouch_young = models.IntegerField(null=True, blank=True, default=0)
    dead_pouch_young = models.IntegerField(null=True, blank=True, default=0)
    alive_unsure = models.IntegerField(null=True, blank=True, default=0)
    dead_unsure = models.IntegerField(null=True, blank=True, default=0)

    class Meta:
        app_label = "boranga"

    def __str__(self):
        return str(self.occurrence_report)


class IdentificationCertainty(models.Model):
    """
    # Admin List
    May be a mandatory field that assessor needs to complete

    Used by:
    - Identification

    """

    name = models.CharField(max_length=250, blank=False, null=False, unique=True)

    class Meta:
        app_label = "boranga"
        verbose_name = "Identification Certainty"
        verbose_name_plural = "Identification Certainties"
        ordering = ["name"]

    def __str__(self):
        return str(self.name)


class SampleType(models.Model):
    """
    # Admin List

    Used by:
    - Identification

    """

    name = models.CharField(max_length=250, blank=False, null=False)
    group_type = models.ForeignKey(
        GroupType, on_delete=models.SET_NULL, null=True, blank=True
    )

    class Meta:
        app_label = "boranga"
        ordering = ["name"]

    def __str__(self):
        return str(self.name)


class SampleDestination(models.Model):
    """
    # Admin List

    Used by:
    - Identification

    """

    name = models.CharField(max_length=250, blank=False, null=False)

    class Meta:
        app_label = "boranga"
        ordering = ["name"]

    def __str__(self):
        return str(self.name)


class PermitType(models.Model):
    """
    # Admin List

    Used by:
    - Identification

    """

    name = models.CharField(max_length=250, blank=False, null=False)
    group_type = models.ForeignKey(
        GroupType, on_delete=models.SET_NULL, null=True, blank=True
    )

    class Meta:
        app_label = "boranga"
        ordering = ["name"]

    def __str__(self):
        return str(self.name)


class OCRIdentification(models.Model):
    """
    Identification data for occurrence report

    Used for:
    - Occurrence Report
    Is:
    - Table
    """

    occurrence_report = models.OneToOneField(
        OccurrenceReport,
        on_delete=models.CASCADE,
        null=True,
        related_name="identification",
    )
    id_confirmed_by = models.CharField(max_length=1000, null=True, blank=True)
    identification_certainty = models.ForeignKey(
        IdentificationCertainty, on_delete=models.SET_NULL, null=True, blank=True
    )
    sample_type = models.ForeignKey(
        SampleType, on_delete=models.SET_NULL, null=True, blank=True
    )
    sample_destination = models.ForeignKey(
        SampleDestination, on_delete=models.SET_NULL, null=True, blank=True
    )
    permit_type = models.ForeignKey(
        PermitType, on_delete=models.SET_NULL, null=True, blank=True
    )
    permit_id = models.CharField(max_length=500, null=True, blank=True)
    collector_number = models.CharField(max_length=500, null=True, blank=True)
    barcode_number = models.CharField(max_length=500, null=True, blank=True)
    identification_comment = models.TextField(null=True, blank=True)

    class Meta:
        app_label = "boranga"

    def __str__(self):
        return str(self.occurrence_report)


class OccurrenceReportDocument(Document):
    document_number = models.CharField(max_length=9, blank=True, default="")
    occurrence_report = models.ForeignKey(
        "OccurrenceReport", related_name="documents", on_delete=models.CASCADE
    )
    _file = models.FileField(
        upload_to=update_occurrence_report_doc_filename,
        max_length=512,
        storage=private_storage,
    )
    input_name = models.CharField(max_length=255, null=True, blank=True)
    can_delete = models.BooleanField(
        default=True
    )  # after initial submit prevent document from being deleted
    can_hide = models.BooleanField(
        default=False
    )  # after initial submit, document cannot be deleted but can be hidden
    hidden = models.BooleanField(default=False)
    # after initial submit prevent document from being deleted
    # Priya alternatively used below visible field in boranga
    visible = models.BooleanField(
        default=True
    )  # to prevent deletion on file system, hidden and still be available in history
    document_category = models.ForeignKey(
        DocumentCategory, null=True, blank=True, on_delete=models.SET_NULL
    )
    document_sub_category = models.ForeignKey(
        DocumentSubCategory, null=True, blank=True, on_delete=models.SET_NULL
    )
    uploaded_by = models.IntegerField(null=True)  # EmailUserRO

    class Meta:
        app_label = "boranga"
        verbose_name = "Occurrence Report Document"

    def save(self, *args, **kwargs):
        # Prefix "D" char to document_number.
        if self.document_number == "":
            force_insert = kwargs.pop("force_insert", False)
            super().save(no_revision=True, force_insert=force_insert)
            new_document_id = f"D{str(self.pk)}"
            self.document_number = new_document_id
            self.save(*args, **kwargs)
        else:
            super().save(*args, **kwargs)

    @transaction.atomic
    def add_documents(self, request, *args, **kwargs):
        # save the files
        data = json.loads(request.data.get("data"))
        # if not data.get('update'):
        #     documents_qs = self.filter(input_name='species_doc', visible=True)
        #     documents_qs.delete()
        for idx in range(data["num_files"]):
            _file = request.data.get("file-" + str(idx))
            self._file = _file
            self.name = _file.name
            self.input_name = data["input_name"]
            self.can_delete = True
            self.save(no_revision=True)
        # end save documents
        self.save(*args, **kwargs)


class ShapefileDocumentQueryset(models.QuerySet):
    """Using a custom manager to make sure shapfiles are removed when a bulk .delete is called
    as having multiple files with the shapefile extensions in the same folder causes issues.
    """

    def delete(self):
        for obj in self:
            obj._file.delete()
        super().delete()


class OccurrenceReportShapefileDocument(Document):
    objects = ShapefileDocumentQueryset.as_manager()
    occurrence_report = models.ForeignKey(
        "OccurrenceReport", related_name="shapefile_documents", on_delete=models.CASCADE
    )
    _file = models.FileField(
        upload_to=update_occurrence_report_doc_filename,
        max_length=512,
        storage=private_storage,
    )
    input_name = models.CharField(max_length=255, null=True, blank=True)
    can_delete = models.BooleanField(
        default=True
    )  # after initial submit prevent document from being deleted
    can_hide = models.BooleanField(
        default=False
    )  # after initial submit, document cannot be deleted but can be hidden
    hidden = models.BooleanField(
        default=False
    )  # after initial submit prevent document from being deleted

    def delete(self):
        if self.can_delete:
            self._file.delete()
            return super().delete()
        logger.info(
            "Cannot delete existing document object after Occurrence Report has been submitted "
            "(including document submitted before Occurrence Report pushback to status Draft): {}".format(
                self.name
            )
        )

    class Meta:
        app_label = "boranga"


class OCRConservationThreat(RevisionedMixin):
    """
    Threat for a occurrence_report in a particular location.

    NB: Maybe make many to many

    Has a:
    - occurrence_report
    Used for:
    - OccurrenceReport
    Is:
    - Table
    """

    occurrence_report = models.ForeignKey(
        OccurrenceReport,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="ocr_threats",
    )
    threat_number = models.CharField(max_length=9, blank=True, default="")
    threat_category = models.ForeignKey(
        ThreatCategory, on_delete=models.CASCADE, default=None, null=True, blank=True
    )
    threat_agent = models.ForeignKey(
        ThreatAgent, on_delete=models.SET_NULL, default=None, null=True, blank=True
    )
    current_impact = models.ForeignKey(
        CurrentImpact, on_delete=models.SET_NULL, default=None, null=True, blank=True
    )
    potential_impact = models.ForeignKey(
        PotentialImpact, on_delete=models.SET_NULL, default=None, null=True, blank=True
    )
    potential_threat_onset = models.ForeignKey(
        PotentialThreatOnset,
        on_delete=models.SET_NULL,
        default=None,
        null=True,
        blank=True,
    )
    comment = models.CharField(max_length=512, default="None")
    date_observed = models.DateField(blank=True, null=True)
    visible = models.BooleanField(
        default=True
    )  # to prevent deletion, hidden and still be available in history

    class Meta:
        app_label = "boranga"

    def __str__(self):
        return str(self.id)  # TODO: is the most appropriate?

    def save(self, *args, **kwargs):
        if self.threat_number == "":
            force_insert = kwargs.pop("force_insert", False)
            super().save(no_revision=True, force_insert=force_insert)
            new_threat_id = f"T{str(self.pk)}"
            self.threat_number = new_threat_id
            self.save(*args, **kwargs)
        else:
            super().save(*args, **kwargs)

    @property
    def source(self):
        return self.occurrence_report.occurrence_report_number


class WildStatus(models.Model):
    name = models.CharField(max_length=250, blank=False, null=False, unique=True)

    class Meta:
        app_label = "boranga"
        verbose_name = "Wild Status"
        verbose_name_plural = "Wild Statuses"
        ordering = ["name"]

    def __str__(self):
        return str(self.name)


class OccurrenceSource(models.Model):
    name = models.CharField(max_length=250, blank=False, null=False, unique=True)

    class Meta:
        app_label = "boranga"
        verbose_name = "Occurrence Source"
        verbose_name_plural = "Occurrence Sources"
        ordering = ["name"]

    def __str__(self):
        return str(self.name)


class OccurrenceManager(models.Manager):
    def get_queryset(self):
        return (
            super()
            .get_queryset()
            .select_related("group_type", "species")
            .annotate(occurrence_report_count=Count("occurrence_reports"))
        )


class Occurrence(RevisionedMixin):

    RELATED_ITEM_CHOICES = [
        ("species", "Species"),
        ("community", "Community"),
        ("occurrence_report", "Occurrence Report"),
    ]

    objects = OccurrenceManager()
    occurrence_number = models.CharField(max_length=9, blank=True, default="")
    occurrence_name = models.CharField(
        max_length=250, blank=True, null=True, unique=True
    )
    group_type = models.ForeignKey(
        GroupType, on_delete=models.PROTECT, null=True, blank=True
    )
    species = models.ForeignKey(
        Species,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="occurrences",
    )
    community = models.ForeignKey(
        Community,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="occurrences",
    )
    effective_from = models.DateTimeField(null=True, blank=True)
    effective_to = models.DateTimeField(null=True, blank=True)
    submitter = models.IntegerField(null=True)  # EmailUserRO
    wild_status = models.ForeignKey(
        WildStatus, on_delete=models.PROTECT, null=True, blank=True
    )
    occurrence_source = models.ForeignKey(
        OccurrenceSource, on_delete=models.PROTECT, null=True, blank=True
    )

    comment = models.TextField(null=True, blank=True)

    review_due_date = models.DateField(null=True, blank=True)
    review_date = models.DateField(null=True, blank=True)
    reviewed_by = models.IntegerField(null=True)  # EmailUserRO
    review_status = models.CharField(
        "Review Status",
        max_length=30,
        choices=OccurrenceReport.REVIEW_STATUS_CHOICES,
        default=OccurrenceReport.REVIEW_STATUS_CHOICES[0][0],
    )

    created_date = models.DateTimeField(auto_now_add=True, null=False, blank=False)
    updated_date = models.DateTimeField(auto_now=True, null=False, blank=False)

    PROCESSING_STATUS_ACTIVE = "active"
    PROCESSING_STATUS_LOCKED = "locked"
    PROCESSING_STATUS_SPLIT = "split"
    PROCESSING_STATUS_COMBINE = "combine"
    PROCESSING_STATUS_HISTORICAL = "historical"
    PROCESSING_STATUS_CHOICES = (
        (PROCESSING_STATUS_ACTIVE, "Active"),
        (PROCESSING_STATUS_LOCKED, "Locked"),
        (PROCESSING_STATUS_SPLIT, "Split"),
        (PROCESSING_STATUS_COMBINE, "Combine"),
        (PROCESSING_STATUS_HISTORICAL, "Historical"),
    )
    processing_status = models.CharField(
        "Processing Status",
        max_length=30,
        choices=PROCESSING_STATUS_CHOICES,
        default=PROCESSING_STATUS_ACTIVE,
    )

    class Meta:
        indexes = [
            models.Index(fields=["group_type"]),
            models.Index(fields=["species"]),
            models.Index(fields=["community"]),
        ]
        app_label = "boranga"

    def save(self, *args, **kwargs):
        if self.occurrence_number == "":
            force_insert = kwargs.pop("force_insert", False)
            super().save(no_revision=True, force_insert=force_insert)
            self.occurrence_number = f"OCC{str(self.pk)}"
            self.save(*args, **kwargs)
        else:
            super().save(*args, **kwargs)

    def __str__(self):
        if self.species:
            return f"{self.occurrence_number} - {self.species} ({self.group_type})"
        elif self.community:
            return f"{self.occurrence_number} - {self.community} ({self.group_type})"
        else:
            return f"{self.occurrence_number} - {self.group_type}"

    @property
    def number_of_reports(self):
        return self.occurrence_report_count

    def can_user_edit(self, user):
        user_editable_state = [
            "active",
        ]
        if self.processing_status not in user_editable_state:
            return False
        else:
            return (
                user.id
                in self.get_occurrence_editor_group().get_system_group_member_ids()
            )

    def get_occurrence_editor_group(self):
        return SystemGroup.objects.get(name=GROUP_NAME_OCCURRENCE_APPROVER)

    def log_user_action(self, action, request):
        return OccurrenceUserAction.log_action(self, action, request.user.id)

    def get_related_occurrence_reports(self, **kwargs):

        return OccurrenceReport.objects.filter(occurrence=self)

    def get_related_items(self, filter_type, **kwargs):
        return_list = []
        if filter_type == "all":
            related_field_names = ["species", "community", "occurrence_report"]
        else:
            related_field_names = [
                filter_type,
            ]
        all_fields = self._meta.get_fields()
        for a_field in all_fields:
            if a_field.name in related_field_names:
                field_objects = []
                if a_field.is_relation:
                    if a_field.many_to_many:
                        field_objects = a_field.related_model.objects.filter(
                            **{a_field.remote_field.name: self}
                        )
                    elif a_field.many_to_one:  # foreign key
                        field_objects = [
                            getattr(self, a_field.name),
                        ]
                    elif a_field.one_to_many:  # reverse foreign key
                        field_objects = a_field.related_model.objects.filter(
                            **{a_field.remote_field.name: self}
                        )
                    elif a_field.one_to_one:
                        if hasattr(self, a_field.name):
                            field_objects = [
                                getattr(self, a_field.name),
                            ]
                for field_object in field_objects:
                    if field_object:
                        related_item = field_object.as_related_item
                        return_list.append(related_item)

        # serializer = RelatedItemsSerializer(return_list, many=True)
        # return serializer.data
        return return_list

    @classmethod
    @transaction.atomic
    def clone_from_occurrence_report(self, occurrence_report):
        occurrence = Occurrence()

        occurrence.group_type = occurrence_report.group_type

        occurrence.species = occurrence_report.species
        occurrence.community = occurrence_report.community

        occurrence.effective_from = occurrence_report.effective_from
        occurrence.effective_to = occurrence_report.effective_to

        occurrence.review_due_date = occurrence_report.review_due_date
        occurrence.review_date = occurrence_report.review_date
        occurrence.reviewed_by = occurrence_report.reviewed_by
        occurrence.review_status = occurrence_report.review_status

        occurrence.save(no_revision=True)

        # Clone all the associated models
        habitat_composition = clone_model(
            OCRHabitatComposition,
            OCCHabitatComposition,
            occurrence_report.habitat_composition,
        )
        if habitat_composition:
            habitat_composition.occurrence = occurrence
            habitat_composition.save()

        habitat_condition = clone_model(
            OCRHabitatCondition,
            OCCHabitatCondition,
            occurrence_report.habitat_condition,
        )
        if habitat_condition:
            habitat_condition.occurrence = occurrence
            habitat_condition.save()

        fire_history = clone_model(
            OCRFireHistory, OCCFireHistory, occurrence_report.fire_history
        )
        clone_model(
            OCRAssociatedSpecies,
            OCCAssociatedSpecies,
            occurrence_report.associated_species,
        )
        if fire_history:
            fire_history.occurrence = occurrence
            fire_history.save()

        observation_detail = clone_model(
            OCRObservationDetail,
            OCCObservationDetail,
            occurrence_report.observation_detail,
        )
        if observation_detail:
            observation_detail.occurrence = occurrence
            observation_detail.save()

        plant_count = clone_model(
            OCRPlantCount, OCCPlantCount, occurrence_report.plant_count
        )
        if plant_count:
            plant_count.occurrence = occurrence
            plant_count.save()

        animal_observation = clone_model(
            OCRAnimalObservation,
            OCCAnimalObservation,
            occurrence_report.animal_observation,
        )
        if animal_observation:
            animal_observation.occurrence = occurrence
            animal_observation.save()

        identification = clone_model(
            OCRIdentification, OCCIdentification, occurrence_report.identification
        )
        if identification:
            identification.occurrence = occurrence
            identification.save()

        # Clone the threats
        for threat in occurrence_report.ocr_threats.all():
            occ_threat = clone_model(
                OCRConservationThreat, OCCConservationThreat, threat
            )
            if occ_threat:
                occ_threat.occurrence = occurrence
                occ_threat.occurrence_report_threat = threat
                occ_threat.save()

        # Clone the documents
        for doc in occurrence_report.documents.all():
            occ_doc = clone_model(OccurrenceReportDocument, OccurrenceDocument, doc)
            if occ_doc:
                occ_doc.occurrence = occurrence
                occ_doc.save()

        # TODO: Once occurrence has it's own geometry field, clone the geometry here

        # TODO: Make sure everything else is cloned once OCR/OCC sections are finalised

        return occurrence


class OccurrenceLogEntry(CommunicationsLogEntry):
    occurrence = models.ForeignKey(
        Occurrence, related_name="comms_logs", on_delete=models.CASCADE
    )

    def __str__(self):
        return f"{self.reference} - {self.subject}"

    class Meta:
        app_label = "boranga"

    def save(self, **kwargs):
        # save the occurrence number as the reference if the reference not provided
        if not self.reference:
            self.reference = self.occurrence.occurrence_number
        super().save(**kwargs)


def update_occurrence_comms_log_filename(instance, filename):
    return "{}/occurrence/{}/communications/{}".format(
        settings.MEDIA_APP_DIR, instance.log_entry.occurrence.id, filename
    )


class OccurrenceLogDocument(Document):
    log_entry = models.ForeignKey(
        OccurrenceLogEntry, related_name="documents", on_delete=models.CASCADE
    )
    _file = models.FileField(
        upload_to=update_occurrence_comms_log_filename,
        max_length=512,
        storage=private_storage,
    )

    class Meta:
        app_label = "boranga"


class OccurrenceUserAction(UserAction):
    ACTION_VIEW_OCCURRENCE = "View occurrence {}"
    ACTION_SAVE_OCCURRENCE = "Save occurrence {}"
    ACTION_EDIT_OCCURRENCE = "Edit occurrence {}"

    # Document
    ACTION_ADD_DOCUMENT = "Document {} added for occurrence {}"
    ACTION_UPDATE_DOCUMENT = "Document {} updated for occurrence {}"
    ACTION_DISCARD_DOCUMENT = "Document {} discarded for occurrence {}"
    ACTION_REINSTATE_DOCUMENT = "Document {} reinstated for occurrence {}"

    # Threat
    ACTION_ADD_THREAT = "Threat {} added for occurrence {}"
    ACTION_UPDATE_THREAT = "Threat {} updated for occurrence {}"
    ACTION_DISCARD_THREAT = "Threat {} discarded for occurrence {}"
    ACTION_REINSTATE_THREAT = "Threat {} reinstated for occurrence {}"

    class Meta:
        app_label = "boranga"
        ordering = ("-when",)

    @classmethod
    def log_action(cls, occurrence, action, user):
        return cls.objects.create(occurrence=occurrence, who=user, what=str(action))

    occurrence = models.ForeignKey(
        Occurrence, related_name="action_logs", on_delete=models.CASCADE
    )


class OccurrenceDocument(Document):
    document_number = models.CharField(max_length=9, blank=True, default="")
    occurrence = models.ForeignKey(
        "Occurrence", related_name="documents", on_delete=models.CASCADE
    )
    _file = models.FileField(
        upload_to=update_occurrence_doc_filename,
        max_length=512,
        storage=private_storage,
    )
    input_name = models.CharField(max_length=255, null=True, blank=True)
    can_delete = models.BooleanField(
        default=True
    )  # after initial submit prevent document from being deleted
    can_hide = models.BooleanField(
        default=False
    )  # after initial submit, document cannot be deleted but can be hidden
    hidden = models.BooleanField(default=False)
    # after initial submit prevent document from being deleted
    # Priya alternatively used below visible field in boranga
    visible = models.BooleanField(
        default=True
    )  # to prevent deletion on file system, hidden and still be available in history
    document_category = models.ForeignKey(
        DocumentCategory, null=True, blank=True, on_delete=models.SET_NULL
    )
    document_sub_category = models.ForeignKey(
        DocumentSubCategory, null=True, blank=True, on_delete=models.SET_NULL
    )
    uploaded_by = models.IntegerField(null=True)  # EmailUserRO

    class Meta:
        app_label = "boranga"
        verbose_name = "Occurrence Document"

    def save(self, *args, **kwargs):
        # Prefix "D" char to document_number.
        if self.document_number == "":
            force_insert = kwargs.pop("force_insert", False)
            super().save(no_revision=True, force_insert=force_insert)
            new_document_id = f"D{str(self.pk)}"
            self.document_number = new_document_id
            self.save(*args, **kwargs)
        else:
            super().save(*args, **kwargs)

    @transaction.atomic
    def add_documents(self, request, *args, **kwargs):
        # save the files
        data = json.loads(request.data.get("data"))
        # if not data.get('update'):
        #     documents_qs = self.filter(input_name='species_doc', visible=True)
        #     documents_qs.delete()
        for idx in range(data["num_files"]):
            _file = request.data.get("file-" + str(idx))
            self._file = _file
            self.name = _file.name
            self.input_name = data["input_name"]
            self.can_delete = True
            self.save(no_revision=True)
        # end save documents
        self.save(*args, **kwargs)


# TODO keep for now, remove if not needed (depends on what requirements are for OCR/OCC location)
class OCCObserverDetail(models.Model):
    """
    Observer data  for occurrence

    Used for:
    - Occurrence
    Is:
    - Table
    """

    occurrence = models.ForeignKey(
        Occurrence,
        on_delete=models.CASCADE,
        null=True,
        related_name="observer_detail",
    )
    observer_name = models.CharField(max_length=250, blank=True, null=True)
    role = models.CharField(max_length=250, blank=True, null=True)
    contact = models.CharField(max_length=250, blank=True, null=True)
    organisation = models.CharField(max_length=250, blank=True, null=True)
    main_observer = models.BooleanField(null=True, blank=True)

    class Meta:
        app_label = "boranga"
        unique_together = (
            "observer_name",
            "occurrence",
        )

    def __str__(self):
        return str(self.occurrence)  # TODO: is the most appropriate?


class OCCConservationThreat(RevisionedMixin):
    """
    Threat for an occurrence in a particular location.

    NB: Maybe make many to many

    Has a:
    - occurrence
    Used for:
    - Occurrence
    Is:
    - Table
    """

    occurrence = models.ForeignKey(
        Occurrence,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="occ_threats",
    )

    # original ocr, if any
    occurrence_report_threat = models.ForeignKey(
        OCRConservationThreat,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="original_report_threat",
    )

    threat_number = models.CharField(max_length=9, blank=True, default="")
    threat_category = models.ForeignKey(
        ThreatCategory, on_delete=models.CASCADE, default=None, null=True, blank=True
    )
    threat_agent = models.ForeignKey(
        ThreatAgent, on_delete=models.SET_NULL, default=None, null=True, blank=True
    )
    current_impact = models.ForeignKey(
        CurrentImpact, on_delete=models.SET_NULL, default=None, null=True, blank=True
    )
    potential_impact = models.ForeignKey(
        PotentialImpact, on_delete=models.SET_NULL, default=None, null=True, blank=True
    )
    potential_threat_onset = models.ForeignKey(
        PotentialThreatOnset,
        on_delete=models.SET_NULL,
        default=None,
        null=True,
        blank=True,
    )
    comment = models.CharField(max_length=512, default="None")
    date_observed = models.DateField(blank=True, null=True)
    visible = models.BooleanField(
        default=True
    )  # to prevent deletion, hidden and still be available in history

    class Meta:
        app_label = "boranga"
        unique_together = (
            "occurrence",
            "occurrence_report_threat",
        )

    def __str__(self):
        return str(self.id)  # TODO: is the most appropriate?

    def save(self, *args, **kwargs):
        if self.threat_number == "":
            force_insert = kwargs.pop("force_insert", False)
            super().save(no_revision=True, force_insert=force_insert)
            new_threat_id = f"T{str(self.pk)}"
            self.threat_number = new_threat_id
            self.save(*args, **kwargs)
        else:
            super().save(*args, **kwargs)

    @property
    def source(self):
        if self.occurrence_report_threat:
            return (
                self.occurrence_report_threat.occurrence_report.occurrence_report_number
            )
        return self.occurrence.occurrence_number


class OCCHabitatComposition(models.Model):
    """
    Habitat data for occurrence

    Used for:
    - Occurrence
    Is:
    - Table
    """

    occurrence = models.OneToOneField(
        Occurrence,
        on_delete=models.CASCADE,
        null=True,
        related_name="habitat_composition",
    )
    land_form = MultiSelectField(max_length=250, blank=True, choices=[], null=True)
    rock_type = models.ForeignKey(
        RockType, on_delete=models.SET_NULL, null=True, blank=True
    )
    loose_rock_percent = models.IntegerField(
        null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(100)]
    )
    soil_type = models.ForeignKey(
        SoilType, on_delete=models.SET_NULL, null=True, blank=True
    )
    soil_colour = models.ForeignKey(
        SoilColour, on_delete=models.SET_NULL, null=True, blank=True
    )
    soil_condition = models.ForeignKey(
        SoilCondition, on_delete=models.SET_NULL, null=True, blank=True
    )
    drainage = models.ForeignKey(
        Drainage, on_delete=models.SET_NULL, null=True, blank=True
    )
    water_quality = models.CharField(max_length=500, null=True, blank=True)
    habitat_notes = models.CharField(max_length=1000, null=True, blank=True)

    class Meta:
        app_label = "boranga"

    def __str__(self):
        return str(self.occurrence)  # TODO: is the most appropriate?\


class OCCHabitatCondition(models.Model):
    """
    Habitat Condition data for occurrence

    Used for:
    - Occurrence
    Is:
    - Table
    """

    occurrence = models.OneToOneField(
        Occurrence,
        on_delete=models.CASCADE,
        null=True,
        related_name="habitat_condition",
    )
    pristine = models.IntegerField(
        null=True,
        blank=True,
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    excellent = models.IntegerField(
        null=True,
        blank=True,
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    very_good = models.IntegerField(
        null=True,
        blank=True,
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    good = models.IntegerField(
        null=True,
        blank=True,
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    degraded = models.IntegerField(
        null=True,
        blank=True,
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    completely_degraded = models.IntegerField(
        null=True,
        blank=True,
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )

    class Meta:
        app_label = "boranga"

    def __str__(self):
        return str(self.occurrence)


class OCCFireHistory(models.Model):
    """
    Fire History data for occurrence

    Used for:
    - Occurrence
    Is:
    - Table
    """

    occurrence = models.OneToOneField(
        Occurrence,
        on_delete=models.CASCADE,
        null=True,
        related_name="fire_history",
    )
    last_fire_estimate = models.DateField(null=True, blank=True)
    intensity = models.ForeignKey(
        Intensity, on_delete=models.SET_NULL, null=True, blank=True
    )
    comment = models.CharField(max_length=1000, null=True, blank=True)

    class Meta:
        app_label = "boranga"

    def __str__(self):
        return str(self.occurrence)


class OCCAssociatedSpecies(models.Model):
    """
    Associated Species data for occurrence

    Used for:
    - Occurrence
    Is:
    - Table
    """

    occurrence = models.OneToOneField(
        Occurrence,
        on_delete=models.CASCADE,
        null=True,
        related_name="associated_species",
    )
    related_species = models.TextField(blank=True)

    class Meta:
        app_label = "boranga"

    def __str__(self):
        return str(self.occurrence)


class OCCObservationDetail(models.Model):
    """
    Observation Details data for occurrence

    Used for:
    - Occurrence
    Is:
    - Table
    """

    occurrence = models.OneToOneField(
        Occurrence,
        on_delete=models.CASCADE,
        null=True,
        related_name="observation_detail",
    )
    observation_method = models.ForeignKey(
        ObservationMethod, on_delete=models.SET_NULL, null=True, blank=True
    )
    area_surveyed = models.IntegerField(null=True, blank=True, default=0)
    survey_duration = models.IntegerField(null=True, blank=True, default=0)

    class Meta:
        app_label = "boranga"

    def __str__(self):
        return str(self.occurrence)


class OCCPlantCount(models.Model):
    """
    Plant Count data for occurrence

    Used for:
    - Occurrence
    Is:
    - Table
    """

    occurrence = models.OneToOneField(
        Occurrence,
        on_delete=models.CASCADE,
        null=True,
        related_name="plant_count",
    )
    plant_count_method = models.ForeignKey(
        PlantCountMethod, on_delete=models.SET_NULL, null=True, blank=True
    )
    plant_count_accuracy = models.ForeignKey(
        PlantCountAccuracy, on_delete=models.SET_NULL, null=True, blank=True
    )
    counted_subject = models.ForeignKey(
        CountedSubject, on_delete=models.SET_NULL, null=True, blank=True
    )
    plant_condition = models.ForeignKey(
        PlantCondition, on_delete=models.SET_NULL, null=True, blank=True
    )
    estimated_population_area = models.IntegerField(null=True, blank=True, default=0)

    detailed_alive_mature = models.IntegerField(null=True, blank=True, default=0)
    detailed_dead_mature = models.IntegerField(null=True, blank=True, default=0)
    detailed_alive_juvenile = models.IntegerField(null=True, blank=True, default=0)
    detailed_dead_juvenile = models.IntegerField(null=True, blank=True, default=0)
    detailed_alive_seedling = models.IntegerField(null=True, blank=True, default=0)
    detailed_dead_seedling = models.IntegerField(null=True, blank=True, default=0)
    detailed_alive_unknown = models.IntegerField(null=True, blank=True, default=0)
    detailed_dead_unknown = models.IntegerField(null=True, blank=True, default=0)

    simple_alive = models.IntegerField(null=True, blank=True, default=0)
    simple_dead = models.IntegerField(null=True, blank=True, default=0)

    quadrats_present = models.BooleanField(null=True, blank=True)
    quadrats_data_attached = models.BooleanField(null=True, blank=True)
    quadrats_surveyed = models.IntegerField(null=True, blank=True, default=0)
    individual_quadrat_area = models.IntegerField(null=True, blank=True, default=0)
    total_quadrat_area = models.IntegerField(null=True, blank=True, default=0)
    flowering_plants_per = models.IntegerField(
        null=True,
        blank=True,
        default=0,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )

    clonal_reproduction_present = models.BooleanField(null=True, blank=True)
    vegetative_state_present = models.BooleanField(null=True, blank=True)
    flower_bud_present = models.BooleanField(null=True, blank=True)
    flower_present = models.BooleanField(null=True, blank=True)
    immature_fruit_present = models.BooleanField(null=True, blank=True)
    ripe_fruit_present = models.BooleanField(null=True, blank=True)
    dehisced_fruit_present = models.BooleanField(null=True, blank=True)
    pollinator_observation = models.CharField(max_length=1000, null=True, blank=True)
    comment = models.CharField(max_length=1000, null=True, blank=True)

    class Meta:
        app_label = "boranga"

    def __str__(self):
        return str(self.occurrence)


class OCCAnimalObservation(models.Model):
    """
    Animal Observation data for occurrence

    Used for:
    - Occurrence
    Is:
    - Table
    """

    occurrence = models.OneToOneField(
        Occurrence,
        on_delete=models.CASCADE,
        null=True,
        related_name="animal_observation",
    )
    primary_detection_method = MultiSelectField(
        max_length=250, blank=True, choices=[], null=True
    )
    reproductive_maturity = MultiSelectField(
        max_length=250, blank=True, choices=[], null=True
    )
    animal_health = models.ForeignKey(
        AnimalHealth, on_delete=models.SET_NULL, null=True, blank=True
    )
    death_reason = models.ForeignKey(
        DeathReason, on_delete=models.SET_NULL, null=True, blank=True
    )
    secondary_sign = MultiSelectField(max_length=250, blank=True, choices=[], null=True)

    total_count = models.IntegerField(null=True, blank=True, default=0)
    distinctive_feature = models.CharField(max_length=1000, null=True, blank=True)
    action_taken = models.CharField(max_length=1000, null=True, blank=True)
    action_required = models.CharField(max_length=1000, null=True, blank=True)
    observation_detail_comment = models.CharField(
        max_length=1000, null=True, blank=True
    )

    alive_adult = models.IntegerField(null=True, blank=True, default=0)
    dead_adult = models.IntegerField(null=True, blank=True, default=0)
    alive_juvenile = models.IntegerField(null=True, blank=True, default=0)
    dead_juvenile = models.IntegerField(null=True, blank=True, default=0)
    alive_pouch_young = models.IntegerField(null=True, blank=True, default=0)
    dead_pouch_young = models.IntegerField(null=True, blank=True, default=0)
    alive_unsure = models.IntegerField(null=True, blank=True, default=0)
    dead_unsure = models.IntegerField(null=True, blank=True, default=0)

    class Meta:
        app_label = "boranga"

    def __str__(self):
        return str(self.occurrence)


class OCCIdentification(models.Model):
    """
    Identification data for occurrence

    Used for:
    - Occurrence
    Is:
    - Table
    """

    occurrence = models.OneToOneField(
        Occurrence,
        on_delete=models.CASCADE,
        null=True,
        related_name="identification",
    )
    id_confirmed_by = models.CharField(max_length=1000, null=True, blank=True)
    identification_certainty = models.ForeignKey(
        IdentificationCertainty, on_delete=models.SET_NULL, null=True, blank=True
    )
    sample_type = models.ForeignKey(
        SampleType, on_delete=models.SET_NULL, null=True, blank=True
    )
    sample_destination = models.ForeignKey(
        SampleDestination, on_delete=models.SET_NULL, null=True, blank=True
    )
    permit_type = models.ForeignKey(
        PermitType, on_delete=models.SET_NULL, null=True, blank=True
    )
    permit_id = models.CharField(max_length=500, null=True, blank=True)
    collector_number = models.CharField(max_length=500, null=True, blank=True)
    barcode_number = models.CharField(max_length=500, null=True, blank=True)
    identification_comment = models.TextField(null=True, blank=True)

    class Meta:
        app_label = "boranga"

    def __str__(self):
        return str(self.occurrence)


class OCRExternalRefereeInvite(models.Model):
    email = models.EmailField()
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    datetime_sent = models.DateTimeField(null=True, blank=True)
    datetime_first_logged_in = models.DateTimeField(null=True, blank=True)
    occurrence_report = models.ForeignKey(
        OccurrenceReport,
        related_name="external_referee_invites",
        on_delete=models.CASCADE,
    )
    sent_by = models.IntegerField()
    invite_text = models.TextField(blank=True)
    archived = models.BooleanField(default=False)

    class Meta:
        app_label = "boranga"
        verbose_name = "External Occurrence Report Referral"
        verbose_name_plural = "External Occurrence Report Referrals"

    def __str__(self):
        return_str = f"{self.first_name} {self.last_name} ({self.email})"
        if self.archived:
            return_str += " - Archived"
        return return_str

    def full_name(self):
        return f"{self.first_name} {self.last_name}"


# Occurrence Report Document
reversion.register(OccurrenceReportDocument)

# Occurrence Report Threat
reversion.register(OCRConservationThreat)

# Occurrence Report
reversion.register(OccurrenceReport, follow=["species", "community"])

# Occurrence Document
reversion.register(OccurrenceDocument)

# Occurrence Threat
reversion.register(OCCConservationThreat)

# Occurrence
reversion.register(Occurrence, follow=["species", "community", "occurrence_reports"])
