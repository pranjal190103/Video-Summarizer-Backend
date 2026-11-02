from django.contrib.auth import authenticate, get_user_model
from rest_framework import status
from rest_framework.authtoken.models import Token
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from django.conf import settings
from django.utils import timezone

from .models import SharePointImport, SharePointSyncState, Video
from .serializers import LoginSerializer, RegisterSerializer

User = get_user_model()


def account_payload(user):
    return {'id': user.pk, 'name': user.first_name or user.email, 'email': user.email}


def authenticated_payload(user):
    # A newly authenticated account with no SharePoint library should be picked
    # up by the always-on backend worker promptly, even if another account's
    # last daily sync was only recently completed.
    if (settings.MS_GRAPH_TENANT_ID and settings.MS_GRAPH_CLIENT_ID
            and settings.MS_GRAPH_CLIENT_SECRET and settings.MS_GRAPH_DRIVE_ID
            and settings.MS_GRAPH_FOLDER_ITEM_ID
            and not Video.objects.filter(owner=user, source_type='sharepoint').exists()
            and not SharePointImport.objects.filter(
                owner=user, status__in=['queued', 'processing', 'cancelling'],
            ).exists()):
        state, _ = SharePointSyncState.objects.get_or_create(pk=1)
        SharePointSyncState.objects.filter(pk=state.pk).update(next_sync_at=timezone.now())
    token, _ = Token.objects.get_or_create(user=user)
    return {'token': token.key, 'user': account_payload(user)}


class RegisterView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'register'

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(authenticated_payload(user), status=status.HTTP_201_CREATED)


class LoginView(APIView):
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'login'

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data['email'].strip().lower()
        password = serializer.validated_data['password']
        user = User.objects.filter(email__iexact=email, is_active=True).first()
        authenticated = authenticate(request, username=user.username, password=password) if user else None
        if not authenticated:
            return Response({'detail': 'Email or password is incorrect.'}, status=status.HTTP_400_BAD_REQUEST)
        return Response(authenticated_payload(authenticated))


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        request.user.auth_token.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class CurrentAccountView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(account_payload(request.user))
