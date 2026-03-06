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

