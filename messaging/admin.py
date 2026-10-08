from django.contrib import admin

from .models import Message


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ('id', 'chat', 'sender', 'type', 'short_text', 'is_read', 'created_at')
    list_filter = ('type', 'is_read')
    search_fields = ('text', 'file_name', 'sender__username')
    raw_id_fields = ('chat', 'sender', 'reply_to')
    readonly_fields = ('client_id', 'preview', 'created_at')

    @admin.display(description='text')
    def short_text(self, message):
        return message.text[:60]
