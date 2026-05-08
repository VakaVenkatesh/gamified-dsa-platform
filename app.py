import os
import sqlite3
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify, g
from werkzeug.security import generate_password_hash, check_password_hash
from dotenv import load_dotenv
load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET", "dev-secret-key-change-in-production")

DATABASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'coderealm.db')

def get_db():
    db = getattr(g, '_database', None)
    if db is None:
        print(f"Connecting to database: {DATABASE}")
        db = g._database = sqlite3.connect(DATABASE)
        db.row_factory = sqlite3.Row
    return db

@app.teardown_appcontext
def close_connection(exception):
    db = getattr(g, '_database', None)
    if db is not None:
        db.close()

def get_user_progress():
    if 'username' not in session:
        return None

    db = get_db()
    cursor = db.cursor()
    
    cursor.execute("SELECT id, xp, level FROM users WHERE username = ?", (session['username'],))
    user = cursor.fetchone()
    if not user:
        return None

    user_id = user['id']

    # Count total problems & completed problems in one query
    cursor.execute("""
        SELECT 
            (SELECT COUNT(*) FROM problems) AS total_problems,
            (SELECT COUNT(*) FROM progress WHERE user_id = ? AND completed = 1) AS completed_problems,
            (SELECT COUNT(*) FROM quests) AS total_quests
    """, (user_id,))
    counts = cursor.fetchone()
    total_problems = counts['total_problems']
    completed_problems = counts['completed_problems']
    total_quests = counts['total_quests']

    # Count fully completed quests
    cursor.execute("""
        SELECT COUNT(*) FROM (
            SELECT T1.quest_id
            FROM progress AS T1
            JOIN problems AS T2 ON T1.quest_id = T2.quest_id
            WHERE T1.user_id = ? AND T1.completed = 1
            GROUP BY T1.quest_id
            HAVING COUNT(T1.id) = COUNT(T2.id)
        ) AS completed_quests_table
    """, (user_id,))
    completed_quests = cursor.fetchone()[0]

    # Variable XP per level
    xp_for_current_level_start = sum([200 for lvl in range(1, user['level'])])
    xp_in_current_level = user['xp'] - xp_for_current_level_start
    xp_needed_to_level_up = 200
    xp_percentage = (xp_in_current_level / xp_needed_to_level_up) * 100

    return {
        'level': user['level'],
        'xp': user['xp'],
        'xp_needed': xp_needed_to_level_up - xp_in_current_level,
        'xp_percentage': xp_percentage,
        'completed_problems': completed_problems,
        'total_problems': total_problems,
        'completed_quests': completed_quests,
        'total_quests': total_quests
    }

def check_and_update_level(user_id):
    """
    Checks if a user has leveled up or down and updates their level.
    Returns (level_changed, old_level, new_level).
    """
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT xp, level FROM users WHERE id = ?", (user_id,))
    user = cursor.fetchone()
    
    if not user:
        return False, 0, 0

    old_level = user['level']
    xp = user['xp']
    new_level = old_level

    # Handle level ups
    while xp >= new_level * 200:
        new_level += 1
    # Handle level downs
    while new_level > 1 and xp < (new_level - 1) * 200:
        new_level -= 1

    if new_level != old_level:
        cursor.execute("UPDATE users SET level = ? WHERE id = ?", (new_level, user_id))
        db.commit()
        return True, old_level, new_level

    return False, old_level, new_level

def check_achievements(user_id):
    db = get_db()
    cursor = db.cursor()
    
    cursor.execute("SELECT xp, level FROM users WHERE id = ?", (user_id,))
    user_stats = cursor.fetchone()
    
    cursor.execute("SELECT COUNT(*) FROM progress WHERE user_id = ? AND completed = 1", (user_id,))
    problems_solved = cursor.fetchone()[0]
    
    if not user_stats:
        return []
    
    # Find eligible achievements not yet earned
    cursor.execute("""
        SELECT a.* FROM achievements a
        LEFT JOIN user_achievements ua ON a.id = ua.achievement_id AND ua.user_id = ?
        WHERE ua.id IS NULL
    """, (user_id,))
    
    all_achievements = cursor.fetchall()
    newly_earned = []
    
    for achievement in all_achievements:
        should_award = False
        
        if achievement['requirement_type'] == 'problems_solved':
            should_award = problems_solved >= achievement['requirement_value']
        elif achievement['requirement_type'] == 'level_reached':
            should_award = user_stats['level'] >= achievement['requirement_value']
        elif achievement['requirement_type'] == 'quest_complete':
            if achievement['requirement_value'] == -1:
                # All quests completed
                cursor.execute("SELECT COUNT(*) FROM quests")
                total_quests = cursor.fetchone()[0]
                
                cursor.execute("""
                    SELECT COUNT(DISTINCT quest_id) FROM progress
                    WHERE user_id = ? AND completed = 1
                    GROUP BY quest_id
                    HAVING COUNT(completed) = (SELECT COUNT(*) FROM problems WHERE quest_id = progress.quest_id)
                """, (user_id,))
                
                completed_quests = len(cursor.fetchall())
                should_award = completed_quests == total_quests and total_quests > 0
            else:
                # Specific quest completed
                quest_id = achievement['requirement_value']
                cursor.execute("SELECT COUNT(*) FROM problems WHERE quest_id = ?", (quest_id,))
                total_problems = cursor.fetchone()[0]
                cursor.execute("SELECT COUNT(*) FROM progress WHERE user_id = ? AND quest_id = ? AND completed = 1", (user_id, quest_id))
                completed = cursor.fetchone()[0]
                should_award = completed == total_problems and total_problems > 0
        
        if should_award:
            cursor.execute("INSERT INTO user_achievements (user_id, achievement_id) VALUES (?, ?)", 
                          (user_id, achievement['id']))
            newly_earned.append({
                'name': achievement['name'],
                'icon': achievement['icon']
            })
    
    db.commit()
    return newly_earned


@app.route('/')
def index():
    return render_template('home.html', progress=get_user_progress())

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        if not username or not password:
            flash('Username and password required', 'error')
            return render_template('register.html')
        
        db = get_db()
        cursor = db.cursor()
        cursor.execute("SELECT id FROM users WHERE username = ?", (username,))
        if cursor.fetchone():
            flash('Username exists', 'error')
            return render_template('register.html')
        
        cursor.execute("INSERT INTO users (username, password) VALUES (?, ?)", 
                      (username, generate_password_hash(password)))
        db.commit()
        flash('Registration successful!', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        if not username or not password:
            flash('Username and password required', 'error')
            return render_template('login.html')
        
        db = get_db()
        cursor = db.cursor()
        cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
        user = cursor.fetchone()
        if not user or not check_password_hash(user['password'], password):
            flash('Invalid credentials', 'error')
            return render_template('login.html')
        
        session['username'] = user['username']
        flash(f"Welcome back, {user['username']}!", 'success')
        return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('username', None)
    flash('Logged out', 'info')
    return redirect(url_for('index'))

@app.route('/profile')
def profile():
    if 'username' not in session:
        return redirect(url_for('login'))
    
    # Get recent achievements
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT id FROM users WHERE username = ?", (session['username'],))
    user = cursor.fetchone()
    
    achievements = []
    if user:
        cursor.execute("""
            SELECT a.name, a.description, a.icon, ua.earned_at
            FROM achievements a
            JOIN user_achievements ua ON a.id = ua.achievement_id
            WHERE ua.user_id = ?
            ORDER BY ua.earned_at DESC
        """, (user['id'],))
        achievements = cursor.fetchall()
    
    return render_template('profile.html', progress=get_user_progress(), achievements=achievements)

@app.route('/quests')
def quests():
    if 'username' not in session:
        return redirect(url_for('login'))
    
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT id, level FROM users WHERE username = ?", (session['username'],))
    user = cursor.fetchone()
    
    # Get quests with progress
    cursor.execute("""
        SELECT q.id, q.quest_key, q.name, q.description, q.unlock_level,
               COUNT(p.id) as total_problems
        FROM quests q
        LEFT JOIN problems p ON q.id = p.quest_id
        GROUP BY q.id
        ORDER BY q.unlock_level
    """)
    
    quests_data = []
    for quest in cursor.fetchall():
        # Get completed problems for this quest
        cursor.execute("SELECT COUNT(*) FROM progress WHERE user_id = ? AND quest_id = ? AND completed = 1",
                       (user['id'], quest['id']))
        completed = cursor.fetchone()[0]

        # Get problems for this quest
        cursor.execute("""
            SELECT p.name as title, p.difficulty, COALESCE(pr.completed, 0) as completed
            FROM problems p
            LEFT JOIN progress pr ON p.quest_id = pr.quest_id 
                                   AND p.problem_index = pr.problem_index 
                                   AND pr.user_id = ?
            WHERE p.quest_id = ?
            ORDER BY p.problem_index
        """, (user['id'], quest['id']))
        problems = [dict(row) for row in cursor.fetchall()]
        
        total = quest['total_problems'] or 0

       
        unlocked = (user['level'] >= quest['unlock_level'])

        quests_data.append({
            'quest_key': quest['quest_key'],
            'title': quest['name'],
            'description': quest['description'],
            'level_required': quest['unlock_level'],
            'problems': problems,
            'progress': {
                'completed': completed,
                'total': total,
                'percentage': (completed / total * 100) if total > 0 else 0,
                'unlocked': unlocked
            }
        })
    
    return render_template('quests.html', quests=quests_data, user_level=user['level'], progress=get_user_progress())

@app.route("/quest/<quest_key>")
def quest_detail(quest_key):
    if 'username' not in session:
        return redirect(url_for('login'))
    
    db = get_db()
    cursor = db.cursor()
    
    cursor.execute("SELECT * FROM quests WHERE quest_key = ?", (quest_key,))
    quest = cursor.fetchone()
    cursor.execute("SELECT id, xp, level FROM users WHERE username = ?", (session['username'],))
    user = cursor.fetchone()
    
    if not quest or not user:
        return redirect(url_for('quests'))
    
    #Check unlock
    if user['level'] < quest['unlock_level']:
        progress = {
            'level': user['level'],
            'xp': user['xp'],
            'xp_needed': max(0, (user['level'] * 200) - user['xp']),
            'xp_percentage': (user['xp'] % 200) / 200 * 100,
            'completed': 0,
            'total': 0,
            'percentage': 0,
            'unlocked': False
        }
        return render_template("dsa_sheet.html",
                              quest={'title': quest['name'],
                                     'description': quest['description'],
                                     'level_required': quest['unlock_level']},
                              problems=[],
                              progress=progress)

    cursor.execute("""
        SELECT p.* FROM problems p
        WHERE p.quest_id = ?
        ORDER BY p.problem_index
    """, (quest['id'],))
    problems_data = cursor.fetchall()

    cursor.execute("""
        SELECT problem_index, completed
        FROM progress
        WHERE user_id = ? AND quest_id = ?
    """, (user['id'], quest['id']))
    user_progress_data = {row['problem_index']: row['completed'] for row in cursor.fetchall()}

    problems = []
    completed_count = 0
    for prob in problems_data:
        problem_index = prob['problem_index']
        is_completed = user_progress_data.get(problem_index, 0) == 1
        if is_completed:
            completed_count += 1
        problems.append({
            'title': prob['name'],
            'difficulty': prob['difficulty'],
            'completed': is_completed,
            'problem_key': f"{quest_key}_{problem_index}",
            'leetcode_url': prob['link'],
            'xp_reward': prob['xp_reward']
        })

    total = len(problems)
    xp_for_current_level_start = (user['level'] - 1) * 200
    xp_in_current_level = user['xp'] - xp_for_current_level_start
    xp_needed_to_level_up = 200 - xp_in_current_level
    xp_percentage = (xp_in_current_level / 200) * 100

    progress = {
        'level': user['level'],
        'xp': user['xp'],
        'xp_needed': xp_needed_to_level_up,
        'xp_percentage': xp_percentage,
        'completed': completed_count,
        'total': total,
        'percentage': (completed_count / total * 100) if total > 0 else 0,
        'unlocked': True
    }
    
    return render_template("dsa_sheet.html", 
                          quest={'title': quest['name'], 'description': quest['description'], 'level_required': quest['unlock_level']},
                          problems=problems, progress=progress)


@app.route('/toggle_problem', methods=['POST'])
def toggle_problem():
    if 'username' not in session:
        return jsonify({'error': 'Not logged in', 'success': False}), 401

    data = request.get_json()
    problem_key = data.get('problem_key')
    if not problem_key or '_' not in problem_key:
        return jsonify({'error': 'Invalid problem key', 'success': False}), 400

    quest_key, problem_index_str = problem_key.rsplit('_', 1)
    try:
        problem_index = int(problem_index_str)
    except ValueError:
        return jsonify({'error': 'Invalid problem index', 'success': False}), 400

    db = get_db()
    cursor = db.cursor()
    messages = []

    try:
        db.execute("BEGIN IMMEDIATE")

        cursor.execute("SELECT id, xp, level FROM users WHERE username = ?", (session['username'],))
        user = cursor.fetchone()
        cursor.execute("SELECT id, unlock_level FROM quests WHERE quest_key = ?", (quest_key,))
        quest = cursor.fetchone()
        cursor.execute("SELECT xp_reward FROM problems WHERE quest_id = ? AND problem_index = ?", 
                       (quest['id'], problem_index))
        problem = cursor.fetchone()

        if not user or not quest or not problem:
            return jsonify({'error': 'Problem not found', 'success': False}), 404

        user_id, quest_id, xp_reward = user['id'], quest['id'], problem['xp_reward']

        cursor.execute("SELECT completed FROM progress WHERE user_id = ? AND quest_id = ? AND problem_index = ?",
                       (user_id, quest_id, problem_index))
        progress = cursor.fetchone()
        is_completed = bool(progress)

        if is_completed:
            # Mark incomplete
            cursor.execute("DELETE FROM progress WHERE user_id = ? AND quest_id = ? AND problem_index = ?",
                          (user_id, quest_id, problem_index))
            cursor.execute("""
                UPDATE users
                SET xp = CASE WHEN xp - ? < 0 THEN 0 ELSE xp - ? END
                WHERE id = ?
            """, (xp_reward, xp_reward, user_id))
            action = 'uncompleted'
            messages.append({'message': f'Problem marked incomplete. -{xp_reward} XP', 'category': 'warning', 'level_message': False})
        else:
            # Mark complete
            cursor.execute("INSERT OR REPLACE INTO progress (user_id, quest_id, problem_index, completed) VALUES (?, ?, ?, 1)",
                          (user_id, quest_id, problem_index))
            cursor.execute("UPDATE users SET xp = xp + ? WHERE id = ?", (xp_reward, user_id))
            action = 'completed'
            messages.append({'message': f'Problem completed! +{xp_reward} XP', 'category': 'success', 'level_message': False})

        # Check level change
        level_changed, old_level, new_level = check_and_update_level(user_id)
        if level_changed:
            if new_level > old_level:
                messages.append({'message': f'🎉 Level Up! You are now Level {new_level}', 'category': 'success', 'level_message': True})
            else:
                messages.append({'message': f'⚠️ Level Down! You are now Level {new_level}', 'category': 'warning', 'level_message': True})

            # Newly unlocked quests
            if new_level > old_level:
                cursor.execute("SELECT quest_key, name FROM quests WHERE unlock_level > ? AND unlock_level <= ?", 
                               (old_level, new_level))
                newly_unlocked = cursor.fetchall()
                for q in newly_unlocked:
                    messages.append({'message': f'🔓 New Quest Unlocked: {q["name"]}', 'category': 'success', 'level_message': True})

        # Check achievements
        earned_achievements = check_achievements(user_id)
        for ach in earned_achievements:
            messages.append({'message': f'🏆 Achievement Unlocked: {ach["name"]}', 'category': 'success', 'level_message': False})

        # Updated progress for frontend
        cursor.execute("SELECT xp, level FROM users WHERE id = ?", (user_id,))
        updated_user = cursor.fetchone()

        xp_for_current_level_start = sum([200 if lvl != 4 else 100 for lvl in range(1, updated_user['level'])])
        xp_in_current_level = updated_user['xp'] - xp_for_current_level_start
        xp_needed_to_level_up = 200 if updated_user['level'] != 4 else 100
        xp_percentage = (xp_in_current_level / xp_needed_to_level_up) * 100

        cursor.execute("SELECT COUNT(*) FROM problems WHERE quest_id = ?", (quest_id,))
        total_problems = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM progress WHERE user_id = ? AND quest_id = ? AND completed = 1",
                       (user_id, quest_id))
        completed_count = cursor.fetchone()[0]

        progress_data = {
            'level': updated_user['level'],
            'xp': updated_user['xp'],
            'xp_needed': xp_needed_to_level_up - xp_in_current_level,
            'xp_percentage': xp_percentage,
            'completed': completed_count,
            'total': total_problems
        }

        db.commit()

        return jsonify({
            'success': True,
            'action': action,
            'messages': messages,
            'earned_achievements': earned_achievements,
            'progress': progress_data
        })

    except Exception as e:
        db.rollback()
        return jsonify({'error': str(e), 'success': False}), 500

@app.route('/achievements')
def achievements():
    if 'username' not in session:
        return redirect(url_for('login'))
    
    db = get_db()
    cursor = db.cursor()
    cursor.execute("SELECT id FROM users WHERE username = ?", (session['username'],))
    user = cursor.fetchone()
    
    if not user:
        return redirect(url_for('login'))
    
    # Get all achievements with earned status
    cursor.execute("""
        SELECT a.*, 
               CASE WHEN ua.id IS NOT NULL THEN 1 ELSE 0 END as earned,
               ua.earned_at
        FROM achievements a
        LEFT JOIN user_achievements ua ON a.id = ua.achievement_id AND ua.user_id = ?
        ORDER BY earned DESC
    """, (user['id'],))
    
    all_achievements = cursor.fetchall()
    earned_count = sum(1 for ach in all_achievements if ach['earned'])
    
    return render_template('achievements.html', 
                          achievements=all_achievements,
                          earned_count=earned_count,
                          total_count=len(all_achievements),
                          progress=get_user_progress())

@app.route('/leaderboard')
def leaderboard():
    if 'username' not in session:
        return redirect(url_for('login'))

    db = get_db()
    cursor = db.cursor()

    cursor.execute("""
        SELECT u.id, u.username, u.level, u.xp, 
               COUNT(p.id) as problems_solved
        FROM users u
        LEFT JOIN progress p ON u.id = p.user_id AND p.completed = 1
        GROUP BY u.id
        ORDER BY problems_solved DESC, xp DESC
    """)
    leaderboard_data = cursor.fetchall()

    return render_template("leaderboard.html", leaderboard=leaderboard_data, progress=get_user_progress())

@app.route('/profile_data/<int:user_id>')
def profile_data(user_id):
    db = get_db()
    cursor = db.cursor()

    cursor.execute("SELECT id, username, xp, level FROM users WHERE id = ?", (user_id,))
    user = cursor.fetchone()
    if not user:
        return jsonify({"error": "User not found"}), 404

    # Problems solved
    cursor.execute("SELECT COUNT(*) FROM progress WHERE user_id = ? AND completed = 1", (user_id,))
    completed_problems = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM progress WHERE user_id = ?", (user_id,))
    total_problems = cursor.fetchone()[0]

    # Quests completed
    cursor.execute("""
        SELECT COUNT(DISTINCT quest_id) 
        FROM progress 
        WHERE user_id = ? AND completed = 1
    """, (user_id,))
    completed_quests = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM quests")
    total_quests = cursor.fetchone()[0]

    # Achievements
    cursor.execute("""
        SELECT a.name, a.description, a.icon, ua.earned_at
        FROM achievements a
        JOIN user_achievements ua ON a.id = ua.achievement_id
        WHERE ua.user_id = ?
        ORDER BY ua.earned_at DESC
        LIMIT 5
    """, (user_id,))
    achievements = [dict(row) for row in cursor.fetchall()]

    return jsonify({
        "username": user["username"],
        "level": user["level"],
        "xp": user["xp"],
        "completed_problems": completed_problems,
        "total_problems": total_problems,
        "completed_quests": completed_quests,
        "total_quests": total_quests,
        "achievements": achievements,
        "achievements_count": len(achievements)
    })


if __name__ == '__main__':
    debug_mode = os.environ.get('FLASK_DEBUG', '1') == '1'
    host = os.environ.get('FLASK_HOST', '127.0.0.1')
    port = int(os.environ.get('FLASK_PORT', '5000'))

    app.run(host=host, port=port, debug=debug_mode)
