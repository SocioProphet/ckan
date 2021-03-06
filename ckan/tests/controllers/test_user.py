# encoding: utf-8
import mock
import pytest
from bs4 import BeautifulSoup

import ckan.tests.factories as factories
import ckan.tests.helpers as helpers
from ckan import model
from ckan.lib.helpers import url_for
from ckan.lib.mailer import create_reset_key, MailerException


webtest_submit = helpers.webtest_submit
submit_and_follow = helpers.submit_and_follow


def _clear_activities():
    model.Session.query(model.ActivityDetail).delete()
    model.Session.query(model.Activity).delete()
    model.Session.flush()


def _get_user_edit_page(app):
    user = factories.User()
    env = {"REMOTE_USER": user["name"].encode("ascii")}
    response = app.get(url=url_for("user.edit"), extra_environ=env)
    return env, response, user


@pytest.mark.usefixtures("clean_db")
class TestUser(object):
    def test_register_a_user(self, app):
        response = app.get(url=url_for("user.register"))

        form = response.forms["user-register-form"]
        form["name"] = "newuser"
        form["fullname"] = "New User"
        form["email"] = "test@test.com"
        form["password1"] = "TestPassword1"
        form["password2"] = "TestPassword1"
        response = submit_and_follow(app, form, name="save")
        response = response.follow()
        assert 200 == response.status_int

        user = helpers.call_action("user_show", id="newuser")
        assert user["name"] == "newuser"
        assert user["fullname"] == "New User"
        assert not (user["sysadmin"])

    def test_register_user_bad_password(self, app):
        response = app.get(url=url_for("user.register"))

        form = response.forms["user-register-form"]
        form["name"] = "newuser"
        form["fullname"] = "New User"
        form["email"] = "test@test.com"
        form["password1"] = "TestPassword1"
        form["password2"] = ""

        response = form.submit("save")
        assert "The passwords you entered do not match" in response

    def test_create_user_as_sysadmin(self, app):
        admin_pass = "RandomPassword123"
        sysadmin = factories.Sysadmin(password=admin_pass)

        # Have to do an actual login as this test relies on repoze
        #  cookie handling.

        # get the form
        response = app.get("/user/login")
        # ...it's the second one
        login_form = response.forms[1]
        # fill it in
        login_form["login"] = sysadmin["name"]
        login_form["password"] = admin_pass
        # submit it
        login_form.submit("save")

        response = app.get(url=url_for("user.register"))
        assert "user-register-form" in response.forms
        form = response.forms["user-register-form"]
        form["name"] = "newestuser"
        form["fullname"] = "Newest User"
        form["email"] = "test@test.com"
        form["password1"] = "NewPassword1"
        form["password2"] = "NewPassword1"
        response2 = form.submit("save")
        assert "/user/activity" in response2.location

    def test_registered_user_login(self, app):
        """
    Registered user can submit valid login details at /user/login and
    be returned to appropriate place.
    """

        # make a user
        user = factories.User()

        # get the form
        response = app.get("/user/login")
        # ...it's the second one
        login_form = response.forms[1]

        # fill it in
        login_form["login"] = user["name"]
        login_form["password"] = "RandomPassword123"

        # submit it
        submit_response = login_form.submit()
        # let's go to the last redirect in the chain
        final_response = helpers.webtest_maybe_follow(submit_response)

        # the response is the user dashboard, right?
        final_response.mustcontain(
            '<a href="/dashboard/">Dashboard</a>',
            '<span class="username">{0}</span>'.format(user["fullname"]),
        )
        # and we're definitely not back on the login page.
        final_response.mustcontain(no='<h1 class="page-heading">Login</h1>')

    def test_registered_user_login_bad_password(self, app):
        """
    Registered user is redirected to appropriate place if they submit
    invalid login details at /user/login.
    """

        # make a user
        user = factories.User()

        # get the form
        response = app.get("/user/login")
        # ...it's the second one
        login_form = response.forms[1]

        # fill it in
        login_form["login"] = user["name"]
        login_form["password"] = "BadPass1"

        # submit it
        submit_response = login_form.submit()
        # let's go to the last redirect in the chain
        final_response = helpers.webtest_maybe_follow(submit_response)

        # the response is the login page again
        final_response.mustcontain(
            '<h1 class="page-heading">Login</h1>',
            "Login failed. Bad username or password.",
        )
        # and we're definitely not on the dashboard.
        final_response.mustcontain(no='<a href="/dashboard">Dashboard</a>'),
        final_response.mustcontain(
            no='<span class="username">{0}</span>'.format(user["fullname"])
        )

    def test_user_logout_url_redirect(self, app):
        """_logout url redirects to logged out page.

    Note: this doesn't test the actual logout of a logged in user, just
    the associated redirect.
    """

        logout_url = url_for("user.logout")
        logout_response = app.get(logout_url, status=302)
        final_response = helpers.webtest_maybe_follow(logout_response)

        assert "You are now logged out." in final_response

    @pytest.mark.ckan_config("ckan.root_path", "/my/prefix")
    def test_non_root_user_logout_url_redirect(self, app):
        """
    _logout url redirects to logged out page with `ckan.root_path`
    prefixed.

    Note: this doesn't test the actual logout of a logged in user, just
    the associated redirect.
    """

        logout_url = url_for("user.logout")
        # Remove the prefix otherwise the test app won't find the correct route
        logout_url = logout_url.replace("/my/prefix", "")
        logout_response = app.get(logout_url, status=302)
        assert logout_response.status_int == 302
        assert "/my/prefix/user/logout" in logout_response.location

    def test_not_logged_in_dashboard(self, app):

        for route in ["index", "organizations", "datasets", "groups"]:
            app.get(url=url_for(u"dashboard.{}".format(route)), status=403)

    def test_own_datasets_show_up_on_user_dashboard(self, app):
        user = factories.User()
        dataset_title = "My very own dataset"
        factories.Dataset(
            user=user, name="my-own-dataset", title=dataset_title
        )

        env = {"REMOTE_USER": user["name"].encode("ascii")}
        response = app.get(
            url=url_for("dashboard.datasets"), extra_environ=env
        )

        assert dataset_title in response

    def test_other_datasets_dont_show_up_on_user_dashboard(self, app):
        user1 = factories.User()
        user2 = factories.User()
        dataset_title = "Someone else's dataset"
        factories.Dataset(
            user=user1, name="someone-elses-dataset", title=dataset_title
        )

        env = {"REMOTE_USER": user2["name"].encode("ascii")}
        response = app.get(
            url=url_for("dashboard.datasets"), extra_environ=env
        )

        assert not (dataset_title in response)

    def test_user_edit_no_user(self, app):

        response = app.get(url_for("user.edit", id=None), status=400)
        assert "No user specified" in response

    def test_user_edit_unknown_user(self, app):
        """Attempt to read edit user for an unknown user redirects to login
    page."""

        response = app.get(
            url_for("user.edit", id="unknown_person"), status=403
        )

    def test_user_edit_not_logged_in(self, app):
        """Attempt to read edit user for an existing, not-logged in user
    redirects to login page."""

        user = factories.User()
        username = user["name"]
        response = app.get(url_for("user.edit", id=username), status=403)

    def test_edit_user(self, app):
        user = factories.User(password="TestPassword1")

        env = {"REMOTE_USER": user["name"].encode("ascii")}
        response = app.get(url=url_for("user.edit"), extra_environ=env)
        # existing values in the form
        form = response.forms["user-edit-form"]
        assert form["name"].value == user["name"]
        assert form["fullname"].value == user["fullname"]
        assert form["email"].value == user["email"]
        assert form["about"].value == user["about"]
        assert form["activity_streams_email_notifications"].value is None
        assert form["password1"].value == ""
        assert form["password2"].value == ""

        # new values
        # form['name'] = 'new-name'
        form["fullname"] = "new full name"
        form["email"] = "new@example.com"
        form["about"] = "new about"
        form["activity_streams_email_notifications"] = True
        form["old_password"] = "TestPassword1"
        form["password1"] = "NewPass1"
        form["password2"] = "NewPass1"
        response = submit_and_follow(app, form, env, "save")

        user = model.Session.query(model.User).get(user["id"])
        # assert(user.name== 'new-name')
        assert user.fullname == "new full name"
        assert user.email == "new@example.com"
        assert user.about == "new about"
        assert user.activity_streams_email_notifications

    def test_email_change_without_password(self, app):

        env, response, user = _get_user_edit_page(app)

        form = response.forms["user-edit-form"]

        # new values
        form["email"] = "new@example.com"

        # factory returns user with password 'pass'
        form.fields["old_password"][0].value = "Wrong-pass1"

        response = webtest_submit(form, "save", status=200, extra_environ=env)
        assert "Old Password: incorrect password" in response

    def test_email_change_with_password(self, app):

        env, response, user = _get_user_edit_page(app)

        form = response.forms["user-edit-form"]

        # new values
        form["email"] = "new@example.com"

        # factory returns user with password 'pass'
        form.fields["old_password"][0].value = "RandomPassword123"

        response = submit_and_follow(app, form, env, "save")
        assert "Profile updated" in response

    def test_edit_user_logged_in_username_change(self, app):

        user_pass = "TestPassword1"
        user = factories.User(password=user_pass)

        # Have to do an actual login as this test relys on repoze cookie handling.
        # get the form
        response = app.get("/user/login")
        # ...it's the second one
        login_form = response.forms[1]
        # fill it in
        login_form["login"] = user["name"]
        login_form["password"] = user_pass
        # submit it
        login_form.submit()

        # Now the cookie is set, run the test
        response = app.get(url=url_for("user.edit"))
        # existing values in the form
        form = response.forms["user-edit-form"]

        # new values
        form["name"] = "new-name"
        env = {"REMOTE_USER": user["name"].encode("ascii")}
        response = webtest_submit(form, "save", status=200, extra_environ=env)
        assert "That login name can not be modified" in response

    def test_edit_user_logged_in_username_change_by_name(self, app):
        user_pass = "TestPassword1"
        user = factories.User(password=user_pass)

        # Have to do an actual login as this test relys on repoze cookie handling.
        # get the form
        response = app.get("/user/login")
        # ...it's the second one
        login_form = response.forms[1]
        # fill it in
        login_form["login"] = user["name"]
        login_form["password"] = user_pass
        # submit it
        login_form.submit()

        # Now the cookie is set, run the test
        response = app.get(url=url_for("user.edit", id=user["name"]))
        # existing values in the form
        form = response.forms["user-edit-form"]

        # new values
        form["name"] = "new-name"
        env = {"REMOTE_USER": user["name"].encode("ascii")}
        response = webtest_submit(form, "save", status=200, extra_environ=env)
        assert "That login name can not be modified" in response

    def test_edit_user_logged_in_username_change_by_id(self, app):
        user_pass = "TestPassword1"
        user = factories.User(password=user_pass)

        # Have to do an actual login as this test relys on repoze cookie handling.
        # get the form
        response = app.get("/user/login")
        # ...it's the second one
        login_form = response.forms[1]
        # fill it in
        login_form["login"] = user["name"]
        login_form["password"] = user_pass
        # submit it
        login_form.submit()

        # Now the cookie is set, run the test
        response = app.get(url=url_for("user.edit", id=user["id"]))
        # existing values in the form
        form = response.forms["user-edit-form"]

        # new values
        form["name"] = "new-name"
        env = {"REMOTE_USER": user["name"].encode("ascii")}
        response = webtest_submit(form, "save", status=200, extra_environ=env)
        assert "That login name can not be modified" in response

    def test_perform_reset_for_key_change(self, app):
        password = "TestPassword1"
        params = {"password1": password, "password2": password}
        user = factories.User()
        user_obj = helpers.model.User.by_name(user["name"])
        create_reset_key(user_obj)
        key = user_obj.reset_key

        offset = url_for(
            controller="user",
            action="perform_reset",
            id=user_obj.id,
            key=user_obj.reset_key,
        )
        response = app.post(offset, params=params, status=302)
        user_obj = helpers.model.User.by_name(user["name"])  # Update user_obj

        assert key != user_obj.reset_key

    def test_password_reset_correct_password(self, app):
        """
    user password reset attempted with correct old password
    """

        env, response, user = _get_user_edit_page(app)

        form = response.forms["user-edit-form"]

        # factory returns user with password 'RandomPassword123'
        form.fields["old_password"][0].value = "RandomPassword123"
        form.fields["password1"][0].value = "NewPassword1"
        form.fields["password2"][0].value = "NewPassword1"

        response = submit_and_follow(app, form, env, "save")
        assert "Profile updated" in response

    def test_password_reset_incorrect_password(self, app):
        """
    user password reset attempted with invalid old password
    """

        env, response, user = _get_user_edit_page(app)

        form = response.forms["user-edit-form"]

        # factory returns user with password 'RandomPassword123'
        form.fields["old_password"][0].value = "Wrong-Pass1"
        form.fields["password1"][0].value = "NewPassword1"
        form.fields["password2"][0].value = "NewPassword1"

        response = webtest_submit(form, "save", status=200, extra_environ=env)
        assert "Old Password: incorrect password" in response

    def test_user_follow(self, app):

        user_one = factories.User()
        user_two = factories.User()

        env = {"REMOTE_USER": user_one["name"].encode("ascii")}
        follow_url = url_for(
            controller="user", action="follow", id=user_two["id"]
        )
        response = app.post(follow_url, extra_environ=env, status=302)
        response = response.follow()
        assert (
            "You are now following {0}".format(user_two["display_name"])
            in response
        )

    def test_user_follow_not_exist(self, app):
        """Pass an id for a user that doesn't exist"""

        user_one = factories.User()

        env = {"REMOTE_USER": user_one["name"].encode("ascii")}
        follow_url = url_for(controller="user", action="follow", id="not-here")
        response = app.post(follow_url, extra_environ=env, status=302)
        response = response.follow(status=302)
        assert "user/login" in response.headers["location"]

    def test_user_unfollow(self, app):

        user_one = factories.User()
        user_two = factories.User()

        env = {"REMOTE_USER": user_one["name"].encode("ascii")}
        follow_url = url_for(
            controller="user", action="follow", id=user_two["id"]
        )
        app.post(follow_url, extra_environ=env, status=302)

        unfollow_url = url_for("user.unfollow", id=user_two["id"])
        unfollow_response = app.post(
            unfollow_url, extra_environ=env, status=302
        )
        unfollow_response = unfollow_response.follow()

        assert (
            "You are no longer following {0}".format(user_two["display_name"])
            in unfollow_response
        )

    def test_user_unfollow_not_following(self, app):
        """Unfollow a user not currently following"""

        user_one = factories.User()
        user_two = factories.User()

        env = {"REMOTE_USER": user_one["name"].encode("ascii")}
        unfollow_url = url_for("user.unfollow", id=user_two["id"])
        unfollow_response = app.post(
            unfollow_url, extra_environ=env, status=302
        )
        unfollow_response = unfollow_response.follow()

        assert (
            "You are not following {0}".format(user_two["id"])
            in unfollow_response
        )

    def test_user_unfollow_not_exist(self, app):
        """Unfollow a user that doesn't exist."""

        user_one = factories.User()

        env = {"REMOTE_USER": user_one["name"].encode("ascii")}
        unfollow_url = url_for("user.unfollow", id="not-here")
        unfollow_response = app.post(
            unfollow_url, extra_environ=env, status=302
        )
        unfollow_response = unfollow_response.follow(status=302)
        assert "user/login" in unfollow_response.headers["location"]

    def test_user_follower_list(self, app):
        """Following users appear on followers list page."""

        user_one = factories.Sysadmin()
        user_two = factories.User()

        env = {"REMOTE_USER": user_one["name"].encode("ascii")}
        follow_url = url_for(
            controller="user", action="follow", id=user_two["id"]
        )
        app.post(follow_url, extra_environ=env, status=302)

        followers_url = url_for("user.followers", id=user_two["id"])

        # Only sysadmins can view the followers list pages
        followers_response = app.get(
            followers_url, extra_environ=env, status=200
        )
        assert user_one["display_name"] in followers_response

    def test_user_page_anon_access(self, app):
        """Anon users can access the user list page"""

        user_url = url_for("user.index")
        user_response = app.get(user_url, status=200)
        assert "<title>All Users - CKAN</title>" in user_response

    def test_user_page_lists_users(self, app):
        """/users/ lists registered users"""
        initial_user_count = model.User.count()
        factories.User(fullname="User One")
        factories.User(fullname="User Two")
        factories.User(fullname="User Three")

        user_url = url_for("user.index")
        user_response = app.get(user_url, status=200)

        user_response_html = BeautifulSoup(user_response.body)
        user_list = user_response_html.select("ul.user-list li")
        assert len(user_list) == 3 + initial_user_count

        user_names = [u.text.strip() for u in user_list]
        assert "User One" in user_names
        assert "User Two" in user_names
        assert "User Three" in user_names

    def test_user_page_doesnot_list_deleted_users(self, app):
        """/users/ doesn't list deleted users"""
        initial_user_count = model.User.count()

        factories.User(fullname="User One", state="deleted")
        factories.User(fullname="User Two")
        factories.User(fullname="User Three")

        user_url = url_for("user.index")
        user_response = app.get(user_url, status=200)

        user_response_html = BeautifulSoup(user_response.body)
        user_list = user_response_html.select("ul.user-list li")
        assert len(user_list) == 2 + initial_user_count

        user_names = [u.text.strip() for u in user_list]
        assert "User One" not in user_names
        assert "User Two" in user_names
        assert "User Three" in user_names

    def test_user_page_anon_search(self, app):
        """Anon users can search for users by username."""

        factories.User(fullname="User One", email="useroneemail@example.com")
        factories.User(fullname="Person Two")
        factories.User(fullname="Person Three")

        user_url = url_for("user.index")
        user_response = app.get(user_url, status=200)
        search_form = user_response.forms["user-search-form"]
        search_form["q"] = "Person"
        search_response = webtest_submit(search_form, status=200)

        search_response_html = BeautifulSoup(search_response.body)
        user_list = search_response_html.select("ul.user-list li")
        assert len(user_list) == 2

        user_names = [u.text.strip() for u in user_list]
        assert "Person Two" in user_names
        assert "Person Three" in user_names
        assert "User One" not in user_names

    def test_user_page_anon_search_not_by_email(self, app):
        """Anon users can not search for users by email."""

        factories.User(fullname="User One", email="useroneemail@example.com")
        factories.User(fullname="Person Two")
        factories.User(fullname="Person Three")

        user_url = url_for("user.index")
        user_response = app.get(user_url, status=200)
        search_form = user_response.forms["user-search-form"]
        search_form["q"] = "useroneemail@example.com"
        search_response = webtest_submit(search_form, status=200)

        search_response_html = BeautifulSoup(search_response.body)
        user_list = search_response_html.select("ul.user-list li")
        assert len(user_list) == 0

    def test_user_page_sysadmin_user(self, app):
        """Sysadmin can search for users by email."""

        sysadmin = factories.Sysadmin()

        factories.User(fullname="User One", email="useroneemail@example.com")
        factories.User(fullname="Person Two")
        factories.User(fullname="Person Three")

        env = {"REMOTE_USER": sysadmin["name"].encode("ascii")}
        user_url = url_for("user.index")
        user_response = app.get(user_url, status=200, extra_environ=env)
        search_form = user_response.forms["user-search-form"]
        search_form["q"] = "useroneemail@example.com"
        search_response = webtest_submit(
            search_form, status=200, extra_environ=env
        )

        search_response_html = BeautifulSoup(search_response.body)
        user_list = search_response_html.select("ul.user-list li")
        assert len(user_list) == 1
        assert user_list[0].text.strip() == "User One"

    def test_simple(self, app):
        """Checking the template shows the activity stream."""

        user = factories.User()

        url = url_for("user.activity", id=user["id"])
        response = app.get(url)
        assert "Mr. Test User" in response
        assert "signed up" in response

    def test_create_user(self, app):

        user = factories.User()

        url = url_for("user.activity", id=user["id"])
        response = app.get(url)
        assert (
            '<a href="/user/{}">Mr. Test User'.format(user["name"]) in response
        )
        assert "signed up" in response

    def test_change_user(self, app):

        user = factories.User()
        _clear_activities()
        user["fullname"] = "Mr. Changed Name"
        helpers.call_action(
            "user_update", context={"user": user["name"]}, **user
        )

        url = url_for("user.activity", id=user["id"])
        response = app.get(url)
        assert (
            '<a href="/user/{}">Mr. Changed Name'.format(user["name"])
            in response
        )
        assert "updated their profile" in response

    def test_create_dataset(self, app):

        user = factories.User()
        _clear_activities()
        dataset = factories.Dataset(user=user)

        url = url_for("user.activity", id=user["id"])
        response = app.get(url)
        assert (
            '<a href="/user/{}">Mr. Test User'.format(user["name"]) in response
        )
        assert "created the dataset" in response
        assert (
            '<a href="/dataset/{}">Test Dataset'.format(dataset["id"])
            in response
        )

    def test_change_dataset(self, app):

        user = factories.User()
        dataset = factories.Dataset(user=user)
        _clear_activities()
        dataset["title"] = "Dataset with changed title"
        helpers.call_action(
            "package_update", context={"user": user["name"]}, **dataset
        )

        url = url_for("user.activity", id=user["id"])
        response = app.get(url)
        assert (
            '<a href="/user/{}">Mr. Test User'.format(user["name"]) in response
        )
        assert "updated the dataset" in response
        assert (
            '<a href="/dataset/{}">Dataset with changed title'.format(
                dataset["id"]
            )
            in response
        )

    def test_delete_dataset(self, app):

        user = factories.User()
        dataset = factories.Dataset(user=user)
        _clear_activities()
        helpers.call_action(
            "package_delete", context={"user": user["name"]}, **dataset
        )

        url = url_for("user.activity", id=user["id"])
        env = {"REMOTE_USER": user["name"].encode("ascii")}
        response = app.get(url, extra_environ=env)
        assert (
            '<a href="/user/{}">Mr. Test User'.format(user["name"]) in response
        )
        assert "deleted the dataset" in response
        assert (
            '<a href="/dataset/{}">Test Dataset'.format(dataset["id"])
            in response
        )

    def test_create_group(self, app):

        user = factories.User()
        group = factories.Group(user=user)

        url = url_for("user.activity", id=user["id"])
        response = app.get(url)
        assert (
            '<a href="/user/{}">Mr. Test User'.format(user["name"]) in response
        )
        assert "created the group" in response
        assert '<a href="/group/{}">Test Group'.format(group["id"]) in response

    def test_change_group(self, app):

        user = factories.User()
        group = factories.Group(user=user)
        _clear_activities()
        group["title"] = "Group with changed title"
        helpers.call_action(
            "group_update", context={"user": user["name"]}, **group
        )

        url = url_for("user.activity", id=user["id"])
        response = app.get(url)
        assert (
            '<a href="/user/{}">Mr. Test User'.format(user["name"]) in response
        )
        assert "updated the group" in response
        assert (
            '<a href="/group/{}">Group with changed title'.format(group["id"])
            in response
        )

    def test_delete_group_using_group_delete(self, app):

        user = factories.User()
        group = factories.Group(user=user)
        _clear_activities()
        helpers.call_action(
            "group_delete", context={"user": user["name"]}, **group
        )

        url = url_for("user.activity", id=user["id"])
        response = app.get(url)
        assert (
            '<a href="/user/{}">Mr. Test User'.format(user["name"]) in response
        )
        assert "deleted the group" in response
        assert '<a href="/group/{}">Test Group'.format(group["id"]) in response

    def test_delete_group_by_updating_state(self, app):

        user = factories.User()
        group = factories.Group(user=user)
        _clear_activities()
        group["state"] = "deleted"
        helpers.call_action(
            "group_update", context={"user": user["name"]}, **group
        )

        url = url_for("group.activity", id=group["id"])
        env = {"REMOTE_USER": user["name"].encode("ascii")}
        response = app.get(url, extra_environ=env)
        assert (
            '<a href="/user/{}">Mr. Test User'.format(user["name"]) in response
        )
        assert "deleted the group" in response
        assert (
            '<a href="/group/{}">Test Group'.format(group["name"]) in response
        )

    @mock.patch("ckan.lib.mailer.send_reset_link")
    def test_request_reset_by_email(self, send_reset_link, app):
        user = factories.User()

        offset = url_for("user.request_reset")
        response = app.post(
            offset, params=dict(user=user["email"]), status=302
        ).follow()

        assert "A reset link has been emailed to you" in response
        assert send_reset_link.call_args[0][0].id == user["id"]

    @mock.patch("ckan.lib.mailer.send_reset_link")
    def test_request_reset_by_name(self, send_reset_link, app):
        user = factories.User()

        offset = url_for("user.request_reset")
        response = app.post(
            offset, params=dict(user=user["name"]), status=302
        ).follow()

        assert "A reset link has been emailed to you" in response
        assert send_reset_link.call_args[0][0].id == user["id"]

    @mock.patch("ckan.lib.mailer.send_reset_link")
    def test_request_reset_when_duplicate_emails(self, send_reset_link, app):
        user_a = factories.User(email="me@example.com")
        user_b = factories.User(email="me@example.com")

        offset = url_for("user.request_reset")
        response = app.post(
            offset, params=dict(user="me@example.com"), status=302
        ).follow()

        assert "A reset link has been emailed to you" in response
        emailed_users = [
            call[0][0].name for call in send_reset_link.call_args_list
        ]
        assert emailed_users == [user_a["name"], user_b["name"]]

    def test_request_reset_without_param(self, app):

        offset = url_for("user.request_reset")
        response = app.post(offset).follow()

        assert "Email is required" in response

    @mock.patch("ckan.lib.mailer.send_reset_link")
    def test_request_reset_for_unknown_username(self, send_reset_link, app):

        offset = url_for("user.request_reset")
        response = app.post(
            offset, params=dict(user="unknown"), status=302
        ).follow()

        # doesn't reveal account does or doesn't exist
        assert "A reset link has been emailed to you" in response
        send_reset_link.assert_not_called()

    @mock.patch("ckan.lib.mailer.send_reset_link")
    def test_request_reset_for_unknown_email(self, send_reset_link, app):

        offset = url_for("user.request_reset")
        response = app.post(
            offset, params=dict(user="unknown@example.com"), status=302
        ).follow()

        # doesn't reveal account does or doesn't exist
        assert "A reset link has been emailed to you" in response
        send_reset_link.assert_not_called()

    @mock.patch("ckan.lib.mailer.send_reset_link")
    def test_request_reset_but_mailer_not_configured(
        self, send_reset_link, app
    ):
        user = factories.User()

        offset = url_for("user.request_reset")
        # This is the exception when the mailer is not configured:
        send_reset_link.side_effect = MailerException(
            'SMTP server could not be connected to: "localhost" '
            "[Errno 111] Connection refused"
        )
        response = app.post(
            offset, params=dict(user=user["name"]), status=302
        ).follow()

        assert "Error sending the email" in response
