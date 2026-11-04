"""Input validation for the arcade endpoints."""

from rest_framework import serializers


class TokenExchangeSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=512)
    redirect_uri = serializers.URLField(max_length=200)


class SessionRequestSerializer(serializers.Serializer):
    game = serializers.SlugField(max_length=40)


class ScoreSubmitSerializer(serializers.Serializer):
    session_token = serializers.CharField(max_length=2048)
    score = serializers.IntegerField(min_value=0, max_value=2 ** 31 - 1)
    run_stats = serializers.DictField(required=False, default=dict)
    # Capped rather than unbounded: an input log is a few thousand entries for a
    # long run, and an unbounded JSON body is a free denial of service.
    input_log = serializers.ListField(
        child=serializers.DictField(),
        required=False,
        allow_null=True,
        max_length=200000,
    )


class DepartureSerializer(serializers.Serializer):
    discord_user_id = serializers.CharField(max_length=32)
    left_at = serializers.DateTimeField()


class DepartureBatchSerializer(serializers.Serializer):
    departures = DepartureSerializer(many=True)


class FinalizeSerializer(serializers.Serializer):
    class WinnerSerializer(serializers.Serializer):
        discord_user_id = serializers.CharField(max_length=32)
        display_name = serializers.CharField(max_length=64)
        average_placement = serializers.FloatField()
        breakdown = serializers.DictField(required=False, default=dict)

    winners = WinnerSerializer(many=True)


class PostedMessagesSerializer(serializers.Serializer):
    message_ids = serializers.ListField(
        child=serializers.CharField(max_length=32),
        allow_empty=True,
    )


class AlertSerializer(serializers.Serializer):
    enabled = serializers.BooleanField()
