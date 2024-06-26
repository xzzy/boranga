import json
from io import BytesIO

import pandas as pd
from django.conf import settings
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils.dataframe import dataframe_to_rows
from rest_framework import views, viewsets
from rest_framework.decorators import action as detail_route
from rest_framework.decorators import action as list_route
from rest_framework.decorators import renderer_classes
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework_datatables.filters import DatatablesFilterBackend
from rest_framework_datatables.pagination import DatatablesPageNumberPagination

from boranga import helpers
from boranga.components.conservation_status.models import ConservationStatus
from boranga.components.main.api import UserActionLoggingViewset
from boranga.components.meetings.models import (
    AgendaItem,
    Committee,
    CommitteeMembers,
    Meeting,
    MeetingRoom,
    MeetingUserAction,
    Minutes,
)
from boranga.components.meetings.serializers import (
    AgendaItemSerializer,
    CommitteeMembersSerializer,
    CreateMeetingSerializer,
    EditMeetingSerializer,
    ListAgendaItemSerializer,
    ListMeetingSerializer,
    MeetingLogEntrySerializer,
    MeetingSerializer,
    MeetingUserActionSerializer,
    MinutesSerializer,
    SaveMeetingSerializer,
    SaveMinutesSerializer,
)
from boranga.helpers import is_internal


class MeetingFilterBackend(DatatablesFilterBackend):
    def filter_queryset(self, request, queryset, view):
        total_count = queryset.count()

        filter_meeting_type = request.GET.get("meeting_status")
        if queryset.model is Meeting:
            if filter_meeting_type:
                # queryset = queryset.filter(species__group_type__name=filter_group_type)
                # changed to application_type (ie group_type)
                queryset = queryset

        filter_from_start_date = request.GET.get("filter_from_start_date")
        filter_to_start_date = request.GET.get("filter_to_start_date")
        print(filter_to_start_date)
        filter_from_end_date = request.GET.get("filter_from_end_date")
        filter_to_end_date = request.GET.get("filter_to_end_date")
        if queryset.model is Meeting:
            if filter_from_start_date:
                queryset = queryset.filter(start_date__gte=filter_from_start_date)
            if filter_to_start_date:
                queryset = queryset.filter(start_date__lte=filter_to_start_date)

            if filter_from_end_date:
                queryset = queryset.filter(end_date__gte=filter_from_end_date)
            if filter_to_end_date:
                queryset = queryset.filter(end_date__lte=filter_to_end_date)

        filter_meeting_status = request.GET.get("filter_meeting_status")
        if filter_meeting_status and not filter_meeting_status.lower() == "all":
            if queryset.model is Meeting:
                queryset = queryset.filter(processing_status=filter_meeting_status)

        fields = self.get_fields(request)
        ordering = self.get_ordering(request, view, fields)
        queryset = queryset.order_by(*ordering)
        if len(ordering):
            queryset = queryset.order_by(*ordering)

        try:
            queryset = super().filter_queryset(request, queryset, view)
        except Exception as e:
            print(e)
        setattr(view, "_datatables_total_count", total_count)
        return queryset


class MeetingPaginatedViewSet(viewsets.ReadOnlyModelViewSet):
    filter_backends = (MeetingFilterBackend,)
    pagination_class = DatatablesPageNumberPagination
    queryset = Meeting.objects.none()
    serializer_class = ListMeetingSerializer
    page_size = 10

    def get_queryset(self):
        qs = Meeting.objects.none()

        if is_internal(self.request):
            qs = Meeting.objects.all()

        return qs

    @list_route(
        methods=[
            "GET",
        ],
        detail=False,
    )
    def meeting_export(self, request, *args, **kwargs):
        qs = self.get_queryset()
        qs = self.filter_queryset(qs)
        export_format = request.GET.get("export_format")
        allowed_fields = [
            "meeting_number",
            "title",
            "location",
            "start_date",
            "end_date",
            "processing_status",
        ]

        serializer = ListMeetingSerializer(qs, context={"request": request}, many=True)
        serialized_data = serializer.data

        filtered_data = []
        for obj in serialized_data:
            filtered_obj = {
                key: value for key, value in obj.items() if key in allowed_fields
            }
            filtered_data.append(filtered_obj)

        def flatten_dict(d, parent_key="", sep="_"):
            flattened_dict = {}
            for k, v in d.items():
                new_key = parent_key + sep + k if parent_key else k
                if isinstance(v, dict):
                    flattened_dict.update(flatten_dict(v, new_key, sep))
                else:
                    flattened_dict[new_key] = v
            return flattened_dict

        flattened_data = [flatten_dict(item) for item in filtered_data]
        print(flattened_data)
        df = pd.DataFrame(flattened_data)
        new_headings = [
            "Number",
            "Start Date",
            "End Date",
            "Location",
            "Title",
            "Processing Status",
        ]
        df.columns = new_headings
        column_order = [
            "Number",
            "Title",
            "Location",
            "Start Date",
            "End Date",
            "Processing Status",
        ]
        df = df[column_order]

        if export_format is not None:
            if export_format == "excel":
                buffer = BytesIO()
                workbook = Workbook()
                sheet_name = "Sheet1"
                sheet = workbook.active
                sheet.title = sheet_name

                for row in dataframe_to_rows(df, index=False, header=True):
                    sheet.append(row)
                for cell in sheet[1]:
                    cell.font = Font(bold=True)

                workbook.save(buffer)
                buffer.seek(0)
                response = HttpResponse(
                    buffer.read(), content_type="application/vnd.ms-excel"
                )
                response["Content-Disposition"] = (
                    "attachment; filename=DBCA_Meeting.xlsx"
                )
                final_response = response
                buffer.close()
                return final_response

            elif export_format == "csv":
                csv_data = df.to_csv(index=False)
                response = HttpResponse(content_type="text/csv")
                response["Content-Disposition"] = (
                    "attachment; filename=DBCA_Meeting.csv"
                )
                response.write(csv_data)
                return response

            else:
                return Response(status=400, data="Format not valid")


class MeetingViewSet(UserActionLoggingViewset):
    queryset = Meeting.objects.none()
    serializer_class = MeetingSerializer

    def get_queryset(self):
        if is_internal(self.request):
            qs = Meeting.objects.all()
            return qs
        return Meeting.objects.none()

    def create(self, request, *args, **kwargs):
        serializer = CreateMeetingSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()
        instance.log_user_action(
            MeetingUserAction.ACTION_CREATE_MEETING.format(instance.meeting_number),
            request,
        )
        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    @detail_route(
        methods=[
            "GET",
        ],
        detail=True,
    )
    def internal_meeting(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.log_user_action(
            settings.ACTION_VIEW.format(
                instance._meta.verbose_name.title(),
                helpers.get_instance_identifier(instance),
            ),
            request,
        )
        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    @detail_route(methods=["post"], detail=True)
    @renderer_classes((JSONRenderer,))
    @transaction.atomic
    def meeting_save(self, request, *args, **kwargs):
        instance = self.get_object()
        request_data = request.data
        # to resolve error for serializer submitter id as object is received in request
        if request_data["submitter"]:
            request.data["submitter"] = "{}".format(request_data["submitter"].get("id"))
        serializer = SaveMeetingSerializer(instance, data=request_data, partial=True)

        serializer.is_valid(raise_exception=True)
        if serializer.is_valid():
            saved_instance = serializer.save()
            # add the committee selected members to the meeting
            saved_instance.selected_committee_members.set(
                request_data.get("sel_committee_members_arr")
            )

            instance.log_user_action(
                MeetingUserAction.ACTION_SAVE_MEETING.format(instance.meeting_number),
                request,
            )

        return redirect(reverse("internal"))

    @detail_route(methods=["post"], detail=True)
    @renderer_classes((JSONRenderer,))
    def submit(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.submit(request, self)
        instance.save()
        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    @detail_route(methods=["post"], detail=True)
    def edit_meeting(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = self.get_serializer(instance)
        serializer = EditMeetingSerializer(instance, data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    # used form the meeting Queue section datatable to show the agenda items for the meeting
    @detail_route(
        methods=[
            "GET",
        ],
        detail=True,
    )
    def fetch_agenda_items(self, request, *args, **kwargs):
        instance = self.get_object()
        qs = instance.agenda_items.all()
        serializer = ListAgendaItemSerializer(qs, many=True)
        return Response(serializer.data)

    @detail_route(
        methods=[
            "GET",
        ],
        detail=True,
    )
    def export_agenda_items(self, request, *args, **kwargs):
        instance = self.get_object()
        qs = instance.agenda_items.all()
        serializer = ListAgendaItemSerializer(qs, many=True)
        export_format = request.GET.get("export_format")
        allowed_fields = ["group_type", "scientific_name", "conservation_status_number"]
        serialized_data = serializer.data

        filtered_data = []
        for i, obj in enumerate(serialized_data):
            filtered_obj = {
                key: value for key, value in obj.items() if key in allowed_fields
            }
            filtered_obj["Number"] = i + 1  # Assign sequential numbers starting from 1
            filtered_data.append(filtered_obj)

        df = pd.DataFrame(filtered_data)
        new_headings = [
            "Group Type",
            "Conservation Status Number",
            "Scientific Name",
            "Number",
        ]
        df.columns = new_headings
        column_order = [
            "Number",
            "Group Type",
            "Scientific Name",
            "Conservation Status Number",
        ]
        df = df[column_order]

        if export_format is not None:
            if export_format == "excel":
                buffer = BytesIO()
                workbook = Workbook()
                sheet_name = "Sheet1"
                sheet = workbook.active
                sheet.title = sheet_name

                for row in dataframe_to_rows(df, index=False, header=True):
                    sheet.append(row)
                for cell in sheet[1]:
                    cell.font = Font(bold=True)

                workbook.save(buffer)

                buffer.seek(0)
                response = HttpResponse(
                    buffer.read(), content_type="application/vnd.ms-excel"
                )
                response["Content-Disposition"] = (
                    "attachment; filename=DBCA_Meeting_AgendaItems.xlsx"
                )
                final_response = response
                buffer.close()
                return final_response

            elif export_format == "csv":
                csv_data = df.to_csv(index=False)
                response = HttpResponse(content_type="text/csv")
                response["Content-Disposition"] = (
                    "attachment; filename=DBCA_Meeting_AgendaItems.csv"
                )
                response.write(csv_data)
                return response

            else:
                return Response(status=400, data="Format not valid")

    # used to add the conservation status to the meeting agenda  items
    @detail_route(methods=["post"], detail=True)
    @renderer_classes((JSONRenderer,))
    @transaction.atomic
    def add_agenda_item(self, request, *args, **kwargs):
        instance = self.get_object()
        request_data = request.data
        if request_data["conservation_status_id"]:
            cs = ConservationStatus.objects.get(
                id=request_data["conservation_status_id"]
            )
            instance.agenda_items.create(conservation_status=cs)
        agenda_items = [cs.conservation_status_id for cs in instance.agenda_items.all()]
        return Response(agenda_items)

    # used to remove the conservation status from the meeting agenda  items
    @detail_route(methods=["post"], detail=True)
    @renderer_classes((JSONRenderer,))
    @transaction.atomic
    def remove_agenda_item(self, request, *args, **kwargs):
        instance = self.get_object()
        request_data = request.data
        if request_data["conservation_status_id"]:
            cs = ConservationStatus.objects.get(
                id=request_data["conservation_status_id"]
            )
            agenda_item = AgendaItem.objects.get(
                meeting=instance, conservation_status=cs
            )
            agenda_item.delete()
        agenda_items = [cs.conservation_status_id for cs in instance.agenda_items.all()]
        return Response(agenda_items)

    @detail_route(
        methods=[
            "GET",
        ],
        detail=True,
    )
    def minutes(self, request, *args, **kwargs):
        instance = self.get_object()
        qs = instance.meeting_minutes.all()
        qs = qs.order_by("-uploaded_date")
        serializer = MinutesSerializer(qs, many=True, context={"request": request})
        return Response(serializer.data)

    @detail_route(
        methods=[
            "GET",
        ],
        detail=True,
    )
    def comms_log(self, request, *args, **kwargs):
        instance = self.get_object()
        qs = instance.comms_logs.all()
        serializer = MeetingLogEntrySerializer(qs, many=True)
        return Response(serializer.data)

    @detail_route(
        methods=[
            "POST",
        ],
        detail=True,
    )
    @renderer_classes((JSONRenderer,))
    @transaction.atomic
    def add_comms_log(self, request, *args, **kwargs):
        instance = self.get_object()
        mutable = request.data._mutable
        request.data._mutable = True
        request.data["meeting"] = f"{instance.id}"
        request.data["staff"] = f"{request.user.id}"
        request.data._mutable = mutable
        serializer = MeetingLogEntrySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        comms = serializer.save()

        # Save the files
        for f in request.FILES:
            document = comms.documents.create()
            document.name = str(request.FILES[f])
            document._file = request.FILES[f]
            document.save()

        return Response(serializer.data)

    @detail_route(
        methods=[
            "GET",
        ],
        detail=True,
    )
    def action_log(self, request, *args, **kwargs):
        instance = self.get_object()
        qs = instance.action_logs.all()
        serializer = MeetingUserActionSerializer(qs, many=True)
        return Response(serializer.data)


class GetMeetingDict(views.APIView):

    def get(self, request, format=None):
        location_list = []
        locations = MeetingRoom.objects.all()
        if locations:
            for option in locations:
                location_list.append(
                    {
                        "id": option.id,
                        "name": option.room_name,
                    }
                )
        meeting_type_list = []
        meeting_type_choices = Meeting.MEETING_TYPE_CHOICES
        for choice in meeting_type_choices:
            meeting_type_list.append({"id": choice[0], "display_name": choice[1]})
        status_list = []
        status_choices = Meeting.PROCESSING_STATUS_CHOICES
        for choice in status_choices:
            status_list.append({"id": choice[0], "display_name": choice[1]})
        committee_list = []
        committees = Committee.objects.all()
        for option in committees:
            committee_list.append({"id": option.id, "name": option.name})
        res_json = {
            "location_list": location_list,
            "meeting_type_list": meeting_type_list,
            "status_list": status_list,
            "committee_list": committee_list,
        }
        res_json = json.dumps(res_json)
        return HttpResponse(res_json, content_type="application/json")


class MinutesViewSet(viewsets.ModelViewSet):
    queryset = Minutes.objects.none()
    serializer_class = MinutesSerializer

    def get_queryset(self):
        if is_internal(self.request):
            qs = Minutes.objects.all().order_by("id")
            return qs
        return Minutes.objects.none()

    @detail_route(
        methods=[
            "GET",
        ],
        detail=True,
    )
    def discard(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.visible = False
        instance.save(version_user=request.user)
        instance.meeting.log_user_action(
            MeetingUserAction.ACTION_DISCARD_MINUTE.format(
                instance.minutes_number, instance.meeting.meeting_number
            ),
            request,
        )
        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    @detail_route(
        methods=[
            "GET",
        ],
        detail=True,
    )
    def reinstate(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.visible = True
        instance.save(version_user=request.user)
        serializer = self.get_serializer(instance)
        instance.meeting.log_user_action(
            MeetingUserAction.ACTION_REINSTATE_MINUTE.format(
                instance.minutes_number, instance.meeting.meeting_number
            ),
            request,
        )
        return Response(serializer.data)

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        serializer = SaveMinutesSerializer(
            instance, data=json.loads(request.data.get("data"))
        )
        serializer.is_valid(raise_exception=True)
        serializer.save(no_revision=True)
        instance.add_minutes_documents(request, version_user=request.user)
        instance.meeting.log_user_action(
            MeetingUserAction.ACTION_UPDATE_MINUTE.format(
                instance.minutes_number, instance.meeting.meeting_number
            ),
            request,
        )
        return Response(serializer.data)

    def create(self, request, *args, **kwargs):
        serializer = SaveMinutesSerializer(data=json.loads(request.data.get("data")))
        serializer.is_valid(raise_exception=True)
        instance = serializer.save(no_revision=True)
        instance.add_minutes_documents(request, version_user=request.user)
        instance.meeting.log_user_action(
            MeetingUserAction.ACTION_ADD_MINUTE.format(
                instance.minutes_number, instance.meeting.meeting_number
            ),
            request,
        )
        return Response(serializer.data)


class CommitteeViewSet(viewsets.ModelViewSet):
    queryset = Committee.objects.none()
    serializer_class = None

    def get_queryset(self):
        if is_internal(self.request):
            qs = Committee.objects.all()
            return qs
        return Committee.objects.none()

    @detail_route(
        methods=[
            "GET",
        ],
        detail=True,
    )
    def committee_members(self, request, *args, **kwargs):
        instance = self.get_object()
        qs = CommitteeMembers.objects.filter(committee=instance)
        # qs = instance.members.all()
        qs = qs.order_by("-first_name")
        serializer = CommitteeMembersSerializer(
            qs, many=True, context={"request": request}
        )
        return Response(serializer.data)


class AgendaItemViewSet(viewsets.ModelViewSet):
    queryset = AgendaItem.objects.none()
    serializer_class = AgendaItemSerializer

    def get_queryset(self):
        if is_internal(self.request):
            qs = AgendaItem.objects.all()
            return qs
        return AgendaItem.objects.none()

    @detail_route(
        methods=[
            "GET",
        ],
        detail=True,
    )
    def move_up(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.up()
        instance.save()
        serializer = self.get_serializer(instance)
        return Response(serializer.data)

    @detail_route(
        methods=[
            "GET",
        ],
        detail=True,
    )
    def move_down(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.down()
        instance.save()
        serializer = self.get_serializer(instance)
        return Response(serializer.data)
