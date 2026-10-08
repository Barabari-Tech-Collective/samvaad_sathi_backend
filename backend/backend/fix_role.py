import re
with open('src/api/routes/users.py', 'r', encoding='utf-8') as f:
    content = f.read()

content = content.replace('role="ADMIN" if is_admin_user(current_user) else "USER",', 'role="SUPER_ADMIN" if getattr(current_user, "email", "") == "superadmin@barabari.org" else "ADMIN" if is_admin_user(current_user) else "USER",')

with open('src/api/routes/users.py', 'w', encoding='utf-8') as f:
    f.write(content)
