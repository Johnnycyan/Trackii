import logging

from django.apps import apps
from django.contrib import messages
from django.contrib.auth.decorators import login_not_required
from django.core.paginator import Paginator
from django.db.models import Count, Exists, F, OuterRef, Q, Subquery
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from app import helpers
from app.models import Item, MediaManager, MediaTypes
from app.providers import services
from lists.forms import CustomListForm
from lists.models import CustomList, CustomListItem, ListRecommendation
from users.models import ListDetailSortChoices, ListSortChoices

logger = logging.getLogger(__name__)


@require_GET
def lists(request):
    """Return the custom list page."""
    # Get parameters from request
    search_query = request.GET.get("q", "")
    page = request.GET.get("page", 1)
    sort_by = request.user.update_preference("lists_sort", request.GET.get("sort"))
    enabled_media_types = request.user.get_enabled_media_types()
    selected_media_type = request.GET.get("media_type", "all")

    if selected_media_type != "all" and selected_media_type not in enabled_media_types:
        selected_media_type = "all"

    custom_lists = CustomList.objects.get_user_lists(request.user)

    if search_query:
        custom_lists = custom_lists.filter(
            Q(name__icontains=search_query) | Q(description__icontains=search_query),
        )

    if selected_media_type != "all":
        custom_lists = custom_lists.annotate(
            has_media_type=Exists(
                CustomListItem.objects.filter(
                    custom_list_id=OuterRef("pk"),
                    item__media_type=selected_media_type,
                ),
            ),
        ).filter(has_media_type=True)

    if sort_by == "name":
        custom_lists = custom_lists.order_by("name")
    elif sort_by == "items_count":
        custom_lists = custom_lists.annotate(
            items_count=Count("items", distinct=True),
        ).order_by("-items_count")
    elif sort_by == "newest_first":
        custom_lists = custom_lists.order_by("-id")
    else:  # last_item_added is the default
        # Get the latest update date for each list
        custom_lists = custom_lists.annotate(
            latest_update=Subquery(
                CustomListItem.objects.filter(
                    custom_list=OuterRef("pk"),
                )
                .order_by("-date_added")
                .values("date_added")[:1],
            ),
        ).order_by("-latest_update", "name")

    items_per_page = 20
    paginator = Paginator(custom_lists, items_per_page)
    lists_page = paginator.get_page(page)

    # Create a form for each list
    # needs unique id for django-select2
    for i, custom_list in enumerate(lists_page, start=1):
        custom_list.form = CustomListForm(
            instance=custom_list,
            auto_id=f"id_{i}_%s",
        )

    if request.headers.get("HX-Request"):
        return render(
            request,
            "lists/components/list_grid.html",
            {
                "custom_lists": lists_page,
            },
        )

    create_list_form = CustomListForm()

    return render(
        request,
        "lists/custom_lists.html",
        {
            "custom_lists": lists_page,
            "form": create_list_form,
            "current_sort": sort_by,
            "sort_choices": ListSortChoices.choices,
            "media_types": enabled_media_types,
            "current_media_type": selected_media_type,
        },
    )


@login_not_required
@require_GET
def list_detail(request, list_id):
    """Return the detail page of a custom list."""
    custom_list = get_object_or_404(
        CustomList.objects.select_related("owner").prefetch_related("collaborators"),
        id=list_id,
    )

    # Check if user can view (handles both authenticated and anonymous)
    user = request.user if request.user.is_authenticated else None
    if not custom_list.user_can_view(user):
        msg = "List not found"
        raise Http404(msg)

    # Determine if user can edit (for showing edit controls)
    can_edit = custom_list.user_can_edit(user) if user else False
    is_public_view = not can_edit and custom_list.is_public

    # Get and process request parameters
    # For public views, use defaults instead of user preferences
    if user and can_edit:
        sort_by = user.update_preference(
            "list_detail_sort",
            request.GET.get("sort"),
        )
    else:
        sort_by = request.GET.get("sort", "date_added")

    params = {
        "sort_by": sort_by,
        "media_type": request.GET.get("type", "all"),
        "page": int(request.GET.get("page", 1)),
        "search_query": request.GET.get("q", ""),
    }

    # Build and filter base queryset
    items = custom_list.items.all()
    if params["search_query"]:
        items = items.filter(title__icontains=params["search_query"])
    if params["media_type"] != "all":
        items = items.filter(media_type=params["media_type"])

    # Apply sorting
    sort_mapping = {
        "date_added": ["-customlistitem__date_added"],
        "title": [
            F("title").asc(nulls_last=True),
            F("season_number").asc(nulls_first=True),
            F("episode_number").asc(nulls_first=True),
        ],
        "media_type": ["media_type"],
        "rating": [
            "-customlistitem__date_added",
        ],  # Will be overridden below for rating sort
    }

    # Handle rating sort specially - need to get all items first to sort by rating
    # Only do rating sort for authenticated users who can edit (have media data)
    if params["sort_by"] == "rating" and user and can_edit:
        # Get all items without pagination first
        all_items = items.order_by(
            *sort_mapping.get(params["sort_by"], ["-customlistitem__date_added"]),
        )

        # Get all media objects for rating sort
        media_by_item_id = {}
        media_types_in_all_items = {item.media_type for item in all_items}
        media_manager = MediaManager()

        for media_type in media_types_in_all_items:
            model = apps.get_model("app", media_type)

            if media_type == MediaTypes.EPISODE.value:
                filter_kwargs = {
                    "item_id__in": [item.id for item in all_items],
                    "related_season__user": user,
                }
            else:
                filter_kwargs = {
                    "item_id__in": [item.id for item in all_items],
                    "user": user,
                }

            queryset = model.objects.filter(**filter_kwargs).select_related("item")
            queryset = media_manager._apply_prefetch_related(queryset, media_type)
            media_manager.annotate_max_progress(queryset, media_type)

            # Map media objects by item_id
            for entry in queryset:
                media_by_item_id.setdefault(entry.item_id, entry)

        # Annotate all items with media objects
        for item in all_items:
            item.media = media_by_item_id.get(item.id)

        # Sort all items by rating (score) in descending order,
        # with unrated items at the end
        all_items = sorted(
            all_items,
            key=lambda item: (
                item.media.score if item.media and item.media.score is not None else -1
            ),
            reverse=True,
        )

        # Now paginate the sorted items
        paginator = Paginator(all_items, 16)
        items_page = paginator.get_page(params["page"])
    else:
        # For non-rating sorts, apply database ordering and paginate normally
        items = items.order_by(
            *sort_mapping.get(params["sort_by"], ["-customlistitem__date_added"]),
        )

        # Paginate and prepare media objects
        paginator = Paginator(items, 16)
        items_page = paginator.get_page(params["page"])

        media_by_item_id = {}
        media_types_in_page = {item.media_type for item in items_page}

        media_manager = MediaManager()

        # Only fetch media data for authenticated users who can edit
        if user and can_edit:
            for media_type in media_types_in_page:
                model = apps.get_model("app", media_type)

                if media_type == MediaTypes.EPISODE.value:
                    filter_kwargs = {
                        "item_id__in": [item.id for item in items_page],
                        "related_season__user": user,
                    }
                else:
                    filter_kwargs = {
                        "item_id__in": [item.id for item in items_page],
                        "user": user,
                    }

                queryset = model.objects.filter(**filter_kwargs).select_related("item")
                queryset = media_manager._apply_prefetch_related(queryset, media_type)
                media_manager.annotate_max_progress(queryset, media_type)

                # Map media objects by item_id
                for entry in queryset:
                    media_by_item_id.setdefault(entry.item_id, entry)

        # Annotate items with media objects
        for item in items_page:
            item.media = media_by_item_id.get(item.id)

    # Get recommendation count for owners/collaborators
    recommendation_count = 0
    if can_edit and custom_list.allow_recommendations:
        recommendation_count = custom_list.recommendations.count()

    # Determine if this is a public view (unauthenticated user viewing public list)
    public_view = not request.user.is_authenticated

    # Base context for both full and partial responses
    context = {
        "user": user,
        "custom_list": custom_list,
        "items": items_page,
        "has_next": items_page.has_next(),
        "next_page_number": items_page.next_page_number()
        if items_page.has_next()
        else None,
        "current_sort": params["sort_by"],
        "sort_choices": ListDetailSortChoices.choices,
        "can_edit": can_edit,
        "is_public_view": is_public_view,
        "public_view": public_view,
        "recommendation_count": recommendation_count,
        "base_template": "base_public.html" if public_view else "base.html",
    }

    # Additional context for full page render
    if not request.headers.get("HX-Request"):
        context.update(
            {
                "form": CustomListForm(instance=custom_list) if can_edit else None,
                "media_types": MediaTypes.values,
                "items_count": paginator.count,
                "collaborators_count": custom_list.collaborators.count() + 1,
            },
        )
        return render(request, "lists/list_detail.html", context)

    # HTMX partial response
    return render(request, "lists/components/media_grid.html", context)


@require_POST
def create(request):
    """Create a new custom list."""
    form = CustomListForm(request.POST)
    if form.is_valid():
        custom_list = form.save(commit=False)
        custom_list.owner = request.user
        custom_list.save()
        form.save_m2m()
        logger.info("%s list created successfully.", custom_list)
    else:
        logger.error(form.errors.as_json())
        helpers.form_error_messages(form, request)
    return helpers.redirect_back(request)


@require_POST
def edit(request):
    """Edit an existing custom list."""
    list_id = request.POST.get("list_id")
    custom_list = get_object_or_404(CustomList, id=list_id)
    if custom_list.user_can_edit(request.user):
        form = CustomListForm(request.POST, instance=custom_list)
        if form.is_valid():
            form.save()
            logger.info("%s list edited successfully.", custom_list)
    else:
        messages.error(request, "You do not have permission to edit this list.")
    return helpers.redirect_back(request)


@require_POST
def delete(request):
    """Delete a custom list."""
    list_id = request.POST.get("list_id")
    custom_list = get_object_or_404(CustomList, id=list_id)
    if custom_list.user_can_delete(request.user):
        custom_list.delete()
        logger.info("%s list deleted successfully.", custom_list)
    else:
        messages.error(request, "You do not have permission to delete this list.")
    return helpers.redirect_back(request)


@require_GET
def lists_modal(
    request,
    source,
    media_type,
    media_id,
    season_number=None,
    episode_number=None,
):
    """Return the modal showing all custom lists and allowing to add to them."""
    try:
        item = Item.objects.get(
            media_id=media_id,
            source=source,
            media_type=media_type,
            season_number=season_number,
            episode_number=episode_number,
        )
    except Item.DoesNotExist:
        metadata = services.get_media_metadata(
            media_type,
            media_id,
            source,
            [season_number],
            episode_number,
        )
        item = Item.objects.create(
            media_id=media_id,
            source=source,
            media_type=media_type,
            season_number=season_number,
            episode_number=episode_number,
            title=metadata["title"],
            image=metadata["image"],
        )

    custom_lists = CustomList.objects.get_user_lists_with_item(request.user, item)

    return render(
        request,
        "lists/components/fill_lists.html",
        {"item": item, "custom_lists": custom_lists},
    )


@require_POST
def list_item_toggle(request):
    """Add or remove an item from a custom list."""
    item_id = request.POST["item_id"]
    custom_list_id = request.POST["custom_list_id"]

    item = get_object_or_404(Item, id=item_id)
    custom_list = get_object_or_404(
        CustomList.objects.filter(
            Q(owner=request.user) | Q(collaborators=request.user),
            id=custom_list_id,
        ).distinct(),  # To prevent duplicates, when user is owner and collaborator
    )

    if custom_list.items.filter(id=item.id).exists():
        custom_list.items.remove(item)
        logger.info("%s removed from %s.", item, custom_list)
        has_item = False
    else:
        custom_list.items.add(item)
        logger.info("%s added to %s.", item, custom_list)
        has_item = True

    return render(
        request,
        "lists/components/list_item_button.html",
        {"custom_list": custom_list, "item": item, "has_item": has_item},
    )


# =============================================================================
# Recommendation Views
# =============================================================================


@login_not_required
@require_GET
def recommend_item_page(request, list_id):
    """Show the recommendation search page for a public list."""
    custom_list = get_object_or_404(
        CustomList.objects.select_related("owner"),
        id=list_id,
    )

    if not custom_list.can_recommend():
        msg = "Recommendations are not enabled for this list"
        raise Http404(msg)

    # Get enabled media types - use defaults for anonymous users
    if request.user.is_authenticated:
        enabled_media_types = request.user.get_enabled_media_types()
    else:
        enabled_media_types = MediaTypes.values

    context = {
        "custom_list": custom_list,
        "media_types": enabled_media_types,
        "is_authenticated": request.user.is_authenticated,
        "public_view": not request.user.is_authenticated,
        "base_template": "base_public.html"
        if not request.user.is_authenticated
        else "base.html",
    }

    return render(request, "lists/recommend_item.html", context)


@login_not_required
@require_GET
def recommend_search(request, list_id):
    """Search for items to recommend - returns search results or preview modal."""
    custom_list = get_object_or_404(CustomList, id=list_id)

    if not custom_list.can_recommend():
        return JsonResponse({"error": "Recommendations not enabled"}, status=403)

    # Check if this is a request to show the preview modal
    show_preview = request.GET.get("show_preview")
    if show_preview:
        media_id = request.GET.get("media_id")
        media_type = request.GET.get("media_type")
        source = request.GET.get("source")

        # Fetch full media metadata
        media_metadata = services.get_media_metadata(media_type, media_id, source)

        # Check if already in list or recommended
        from app.models import Item

        item = Item.objects.filter(
            media_id=media_id,
            media_type=media_type,
        ).first()

        already_in_list = False
        already_recommended = False
        if item:
            already_in_list = custom_list.items.filter(id=item.id).exists()
            already_recommended = ListRecommendation.objects.filter(
                custom_list=custom_list,
                item=item,
            ).exists()

        context = {
            "custom_list": custom_list,
            "media": media_metadata,
            "media_id": media_id,
            "media_type": media_type,
            "source": source,
            "is_authenticated": request.user.is_authenticated,
            "already_in_list": already_in_list,
            "already_recommended": already_recommended,
        }
        return render(request, "lists/components/recommend_preview_modal.html", context)

    query = request.GET.get("q", "").strip()
    media_type = request.GET.get("media_type", "tv")
    page = int(request.GET.get("page", 1))

    if not query or len(query) < 2:
        return render(
            request,
            "lists/components/recommend_search_results.html",
            {"results": [], "custom_list": custom_list},
        )

    # Use the existing search service
    from app import config
    from app.models import Item

    source = config.get_default_source_name(media_type).value
    data = services.search(media_type, query, page, source)

    # Get items already in the list (by media_id and source)
    existing_items = set(
        custom_list.items.values_list("media_id", "source"),
    )

    # Get items already recommended (by media_id and source)
    recommended_items = set(
        ListRecommendation.objects.filter(
            custom_list=custom_list,
        ).values_list("item__media_id", "item__source"),
    )

    # Mark results that are already in the list or recommended
    results = data.get("results", [])
    for result in results:
        key = (str(result["media_id"]), result["source"])
        result["already_in_list"] = key in existing_items
        result["already_recommended"] = key in recommended_items

    context = {
        "results": results,
        "custom_list": custom_list,
        "query": query,
        "media_type": media_type,
        "page": page,
        "total_pages": data.get("total_pages", 1),
    }

    return render(request, "lists/components/recommend_search_results.html", context)


@login_not_required
@require_POST
def submit_recommendation(request, list_id):
    """Submit a recommendation for an item to be added to a list."""
    custom_list = get_object_or_404(CustomList, id=list_id)

    if not custom_list.can_recommend():
        messages.error(request, "Recommendations are not enabled for this list.")
        return redirect("list_detail", list_id=list_id)

    # Get item details from the form
    media_id = request.POST.get("media_id")
    media_type = request.POST.get("media_type")
    source = request.POST.get("source")
    season_number = request.POST.get("season_number")
    episode_number = request.POST.get("episode_number")

    # Convert to int if present
    season_number = int(season_number) if season_number else None
    episode_number = int(episode_number) if episode_number else None

    # Get or create the item
    try:
        item = Item.objects.get(
            media_id=media_id,
            source=source,
            media_type=media_type,
            season_number=season_number,
            episode_number=episode_number,
        )
    except Item.DoesNotExist:
        metadata = services.get_media_metadata(
            media_type,
            media_id,
            source,
            [season_number] if season_number else None,
            episode_number,
        )
        item = Item.objects.create(
            media_id=media_id,
            source=source,
            media_type=media_type,
            season_number=season_number,
            episode_number=episode_number,
            title=metadata["title"],
            image=metadata["image"],
        )

    # Check if item is already in the list
    if custom_list.items.filter(id=item.id).exists():
        messages.info(request, f'"{item.title}" is already in this list.')
        return redirect("recommend_item", list_id=list_id)

    # Check if already recommended
    if ListRecommendation.objects.filter(custom_list=custom_list, item=item).exists():
        messages.info(request, f'"{item.title}" has already been recommended.')
        return redirect("recommend_item", list_id=list_id)

    # Create the recommendation
    recommended_by = request.user if request.user.is_authenticated else None
    anonymous_name = ""
    if not request.user.is_authenticated:
        anonymous_name = request.POST.get("anonymous_name", "").strip()[:100]

    ListRecommendation.objects.create(
        custom_list=custom_list,
        item=item,
        recommended_by=recommended_by,
        anonymous_name=anonymous_name,
    )

    logger.info("Recommendation created: %s for %s", item.title, custom_list.name)
    messages.success(
        request,
        f'Your recommendation for "{item.title}" has been submitted!',
    )

    return redirect("public_list_view", list_id=list_id)


@require_GET
def list_recommendations(request, list_id):
    """View all recommendations for a list (owner/collaborators only)."""
    custom_list = get_object_or_404(
        CustomList.objects.select_related("owner").prefetch_related("collaborators"),
        id=list_id,
    )

    if not custom_list.user_can_edit(request.user):
        msg = "You do not have permission to view recommendations for this list"
        raise Http404(msg)

    recommendations = custom_list.recommendations.select_related(
        "item",
        "recommended_by",
    ).order_by("-date_recommended")

    context = {
        "custom_list": custom_list,
        "recommendations": recommendations,
    }

    return render(request, "lists/list_recommendations.html", context)


@require_POST
def approve_recommendation(request, list_id, recommendation_id):
    """Approve a recommendation and add the item to the list."""
    custom_list = get_object_or_404(CustomList, id=list_id)

    if not custom_list.user_can_edit(request.user):
        messages.error(request, "You do not have permission to manage recommendations.")
        return helpers.redirect_back(request)

    recommendation = get_object_or_404(
        ListRecommendation,
        id=recommendation_id,
        custom_list=custom_list,
    )

    # Add item to the list if not already there
    if not custom_list.items.filter(id=recommendation.item.id).exists():
        custom_list.items.add(recommendation.item)
        logger.info(
            "Recommendation approved: %s added to %s",
            recommendation.item.title,
            custom_list.name,
        )
        messages.success(
            request,
            f'"{recommendation.item.title}" has been added to the list.',
        )
    else:
        messages.info(
            request,
            f'"{recommendation.item.title}" is already in the list.',
        )

    # Delete the recommendation
    recommendation.delete()

    return helpers.redirect_back(request)


@require_POST
def deny_recommendation(request, list_id, recommendation_id):
    """Deny/delete a recommendation."""
    custom_list = get_object_or_404(CustomList, id=list_id)

    if not custom_list.user_can_edit(request.user):
        messages.error(request, "You do not have permission to manage recommendations.")
        return helpers.redirect_back(request)

    recommendation = get_object_or_404(
        ListRecommendation,
        id=recommendation_id,
        custom_list=custom_list,
    )

    item_title = recommendation.item.title
    recommendation.delete()

    logger.info("Recommendation denied: %s for %s", item_title, custom_list.name)
    messages.success(request, f'Recommendation for "{item_title}" has been removed.')

    return helpers.redirect_back(request)
