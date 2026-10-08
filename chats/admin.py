from django.contrib import admin

from .models import Chat, ChatMember


class ChatMemberInline(admin.TabularInline):
    model = ChatMember
    extra = 0
    raw_id_fields = ('user',)


@admin.register(Chat)
class ChatAdmin(admin.ModelAdmin):
    list_display = ('id', 'private_key', 'created_at', 'updated_at')
    search_fields = ('private_key', 'participants__username')
    inlines = [ChatMemberInline]
