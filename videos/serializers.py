import uuid
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers
from .models import Video

User = get_user_model()

class RegisterSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=150)
    email = serializers.EmailField()
    password = serializers.CharField(min_length=12, max_length=128, write_only=True, trim_whitespace=False)

    def validate_password(self, value):
        validate_password(value)
        return value

    def validate_email(self, value):
        value = value.strip().lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError('An account with this email already exists.')
        return value

    def create(self, validated_data):
        email = validated_data['email']
        username = f'user_{uuid.uuid4().hex}'
        user = User.objects.create_user(username=username, email=email, password=validated_data['password'], first_name=validated_data['name'].strip())
        return user

class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(max_length=128, write_only=True, trim_whitespace=False)

class VideoSerializer(serializers.ModelSerializer):
    sourceUrl = serializers.URLField(source='source_url', required=False, allow_blank=True)
    sourceType = serializers.CharField(source='source_type', required=False)
    keyTopics = serializers.ListField(source='key_topics', child=serializers.CharField(), required=False)
    kbScore = serializers.IntegerField(source='kb_score', required=False, min_value=0, max_value=100)
    trainingReady = serializers.BooleanField(source='training_ready', required=False)
    analysisMode = serializers.CharField(source='analysis_mode', required=False)
    errorMessage = serializers.CharField(source='error_message', read_only=True)
    createdAt = serializers.DateTimeField(source='created_at', read_only=True)
    updatedAt = serializers.DateTimeField(source='updated_at', read_only=True)
    transcript = serializers.CharField(write_only=True, required=False, allow_blank=True)
    class Meta:
        model = Video
        fields = ['id', 'title', 'sourceUrl', 'sourceType', 'summary', 'transcript', 'categories', 'keyTopics', 'duration', 'language', 'kbScore', 'trainingReady', 'status', 'errorMessage', 'analysisMode', 'createdAt', 'updatedAt']


class VideoDetailSerializer(VideoSerializer):
    transcript = serializers.CharField(required=False, allow_blank=True)
