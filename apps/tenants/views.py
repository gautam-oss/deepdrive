import structlog
from django import forms
from django.shortcuts import redirect, render
from django.utils.text import slugify
from django.views import View

logger = structlog.get_logger(__name__)


class ClinicSignupForm(forms.Form):
    clinic_name = forms.CharField(max_length=255)
    clinic_email = forms.EmailField()
    timezone = forms.ChoiceField(choices=[
        ("UTC", "UTC"),
        ("America/New_York", "Eastern"),
        ("America/Chicago", "Central"),
        ("America/Denver", "Mountain"),
        ("America/Los_Angeles", "Pacific"),
        ("Europe/London", "London"),
        ("Europe/Paris", "Paris"),
        ("Asia/Kolkata", "India"),
        ("Australia/Sydney", "Sydney"),
    ])
    admin_first_name = forms.CharField(max_length=150)
    admin_last_name = forms.CharField(max_length=150)
    admin_email = forms.EmailField()

    def clean_clinic_name(self):
        name = self.cleaned_data["clinic_name"]
        slug = slugify(name)
        if not slug:
            raise forms.ValidationError("Clinic name must produce a valid URL slug.")
        from apps.tenants.models import Clinic
        if Clinic.objects.filter(slug=slug).exists():
            raise forms.ValidationError("A clinic with this name already exists.")
        return name


class ClinicSignupView(View):
    template_name = "tenants/signup.html"

    def get(self, request):
        return render(request, self.template_name, {"form": ClinicSignupForm()})

    def post(self, request):
        form = ClinicSignupForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form})

        data = form.cleaned_data
        slug = slugify(data["clinic_name"])

        from apps.tenants.tasks import provision_clinic
        provision_clinic.delay(
            name=data["clinic_name"],
            slug=slug,
            email=data["clinic_email"],
            timezone=data["timezone"],
            admin_email=data["admin_email"],
            admin_first=data["admin_first_name"],
            admin_last=data["admin_last_name"],
        )

        logger.info("clinic_signup.queued", slug=slug, admin_email=data["admin_email"])
        return redirect("tenants:signup_success")


class ClinicSignupSuccessView(View):
    template_name = "tenants/signup_success.html"

    def get(self, request):
        return render(request, self.template_name)
