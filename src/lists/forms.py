from django import forms
from django_select2 import forms as s2forms

from lists.models import CustomList


class CollaboratorsWidget(s2forms.ModelSelect2MultipleWidget):
    """Custom widget for selecting multiple users."""

    search_fields = ["username__icontains"]


class CustomListForm(forms.ModelForm):
    """Form for creating new custom lists."""

    class Meta:
        """Bind form to model."""

        model = CustomList
        fields = [
            "name",
            "description",
            "collaborators",
            "is_public",
            "allow_recommendations",
        ]
        widgets = {
            "collaborators": CollaboratorsWidget(
                attrs={
                    "data-minimum-input-length": 1,
                    "data-placeholder": "Search users to add...",
                    "data-allow-clear": "false",
                },
            ),
            "is_public": forms.CheckboxInput(
                attrs={
                    "class": "form-checkbox h-5 w-5 text-indigo-600 rounded bg-[#39404b] border-gray-600",
                    "x-model": "isPublic",
                },
            ),
            "allow_recommendations": forms.CheckboxInput(
                attrs={
                    "class": "form-checkbox h-5 w-5 text-indigo-600 rounded bg-[#39404b] border-gray-600",
                    "x-bind:disabled": "!isPublic",
                },
            ),
        }
        labels = {
            "is_public": "Public (read-only access)",
            "allow_recommendations": "Allow recommendations",
        }
        help_texts = {
            "is_public": "Anyone with the link can view this list",
            "allow_recommendations": "Anyone can suggest items to add (requires public)",
        }
