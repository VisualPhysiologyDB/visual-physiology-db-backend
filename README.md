# visual-physiology-db-backend
A repository for all code necessary to host the Visual-Physiology Database


Notes: 

```
# Create the database tables based on your models
python manage.py makemigrations core
python manage.py migrate

# Create your Admin/Caretaker account
python manage.py createsuperuser

# Run the local server
python manage.py runserver

Now you can navigate to `http://localhost:8000/admin` to log in, manually enter an Opsin, mark it as "Approved", and then immediately see it populate dynamically at `http://localhost:8000/api/opsins/`. 

To complete the Github integration later on, you can write a simple Django Management Command (e.g., `python manage.py export_to_github`) that queries all `APPROVED` records, uses Python's `csv` module to recreate your flat files, and pushes them to your GitHub repository via the Git CLI!

If you are looking for more details on managing state choices and custom logic in Django admin workflows, [Event Approval - Django Wednesdays #42](https://www.youtube.com/watch?v=pwkAMX1zgOI) provides a visual walkthrough of implementing an approval boolean/status system in the Django Admin interface.
```

Updating a live production site is slightly different from development. When you were using python manage.py runserver, Django was constantly watching your files and auto-reloading every time you hit "save."

In production, Gunicorn loads your code into the server's memory once and keeps it there. This makes the site incredibly fast, but it means Gunicorn doesn't know when you change a file until you explicitly tell it to reload.

Here is your official workflow for updating the live site whenever you make changes:

The Production Update Workflow
1. Make your changes and get them onto the server
Whether you are editing the files directly on the cluster node (via nano or VS Code SSH) or pulling them from a GitHub repository, get the updated code into your /home/oakley/visual-physiology-db-backend directory.

If you only changed HTML templates (templates/index.html): Skip to Step 4.

If you changed database models (models.py): Proceed to Step 2.

If you changed CSS, JavaScript, or added images: Proceed to Step 3.

If you changed Python logic (views.py, urls.py, etc.): Skip to Step 4.

2. Apply Database Changes (If you edited models.py)
Just like in development, if you change your database structure, you need to apply those changes.

```
python manage.py makemigrations
python manage.py migrate
```

3. Update Static Files (If you added CSS/JS/Images)
If you added new static files, you must tell Django to gather them up and put them in the staticfiles folder for Nginx to serve.

```
python manage.py collectstatic
```

(Type yes when it asks if you want to overwrite existing files).

4. The Magic Command: Restart Gunicorn
To make your new Python code, new HTML templates, or new backend logic go live, you just need to restart the background service we created earlier.

Run this command:

```
sudo systemctl restart gunicorn
```

Summary Checklist (The TL;DR)

Whenever you update your code on the server, just remember this sequence:

```
python manage.py migrate (if database changed)

python manage.py collectstatic (if static files changed)

sudo systemctl restart gunicorn (Always run this!)
```

As soon as you hit enter on that restart command, the live website at visphys.eemb.ucsb.edu will instantly reflect your new code!

