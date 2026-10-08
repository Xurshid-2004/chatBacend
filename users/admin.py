from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import AdminUserCreationForm, UserChangeForm

from .models import User


class UserCreationAdminForm(AdminUserCreationForm):
    class Meta(AdminUserCreationForm.Meta):
        model = User


class UserChangeAdminForm(UserChangeForm):
    class Meta(UserChangeForm.Meta):
        model = User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    form = UserChangeAdminForm
    add_form = UserCreationAdminForm
    fieldsets = DjangoUserAdmin.fieldsets + (
        ('Profile', {'fields': ('avatar', 'bio', 'is_online', 'last_seen')}),
    )
    readonly_fields = ('is_online', 'last_seen', 'last_login', 'date_joined')
    list_display = ('username', 'first_name', 'last_name', 'is_online', 'last_seen', 'is_staff', 'date_joined')
    list_filter = ('is_online', 'is_staff', 'is_superuser', 'is_active')
