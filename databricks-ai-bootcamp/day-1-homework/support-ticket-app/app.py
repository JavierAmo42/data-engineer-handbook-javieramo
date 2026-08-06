import streamlit as st
import psycopg
import os
from databricks import sdk
from psycopg import sql
from psycopg_pool import ConnectionPool

# Database connection setup
workspace_client = sdk.WorkspaceClient()
endpoint = os.getenv("PGENDPOINT", "")
connection_pool = None


class OAuthConnection(psycopg.Connection):
    """Connection subclass that auto-refreshes OAuth credentials."""

    @classmethod
    def connect(cls, conninfo="", **kwargs):
        credential = workspace_client.postgres.generate_database_credential(
            endpoint=endpoint
        )
        kwargs["password"] = credential.token
        return super().connect(conninfo, **kwargs)


def get_connection_pool():
    """Get or create the connection pool."""
    global connection_pool
    if connection_pool is None:
        conn_string = (
            f"dbname={os.getenv('PGDATABASE')} "
            f"user={os.getenv('PGUSER')} "
            f"host={os.getenv('PGHOST')} "
            f"port={os.getenv('PGPORT')} "
            f"sslmode={os.getenv('PGSSLMODE', 'require')} "
            f"application_name={os.getenv('PGAPPNAME')}"
        )
        connection_pool = ConnectionPool(
            conn_string, connection_class=OAuthConnection, min_size=2, max_size=10
        )
    return connection_pool


def get_connection():
    """Get a connection from the pool."""
    return get_connection_pool().connection()


def get_schema_name():
    """Get the schema name in the format {PGAPPNAME}_schema_{PGUSER}."""
    pgappname = os.getenv("PGAPPNAME", "my_app")
    pguser = os.getenv("PGUSER", "").replace('-', '')
    return f"{pgappname}_schema_{pguser}"


def init_database():
    """Create the Lakebase schema and support ticket tables."""

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                schema_name = get_schema_name()

                # Create the schema assigned to this Databricks App.
                cur.execute(
                    sql.SQL("CREATE SCHEMA IF NOT EXISTS {}")
                    .format(sql.Identifier(schema_name))
                )

                # Main support ticket table.
                cur.execute(
                    sql.SQL("""
                        CREATE TABLE IF NOT EXISTS {}.tickets (
                            ticket_id BIGSERIAL PRIMARY KEY,

                            title VARCHAR(200) NOT NULL
                                CHECK (BTRIM(title) <> ''),

                            description TEXT,

                            status VARCHAR(20) NOT NULL DEFAULT 'open'
                                CHECK (
                                    status IN (
                                        'open',
                                        'in_progress',
                                        'resolved'
                                    )
                                ),

                            priority VARCHAR(10) NOT NULL DEFAULT 'medium'
                                CHECK (
                                    priority IN (
                                        'low',
                                        'medium',
                                        'high'
                                    )
                                ),

                            category VARCHAR(50),

                            created_by VARCHAR(100) NOT NULL
                                CHECK (BTRIM(created_by) <> ''),

                            created_at TIMESTAMPTZ NOT NULL
                                DEFAULT CURRENT_TIMESTAMP,

                            updated_at TIMESTAMPTZ NOT NULL
                                DEFAULT CURRENT_TIMESTAMP
                        )
                    """).format(sql.Identifier(schema_name))
                )

                # Messages belonging to a ticket.
                cur.execute(
                    sql.SQL("""
                        CREATE TABLE IF NOT EXISTS {}.ticket_messages (
                            message_id BIGSERIAL PRIMARY KEY,

                            ticket_id BIGINT NOT NULL,

                            message_text TEXT NOT NULL
                                CHECK (BTRIM(message_text) <> ''),

                            author VARCHAR(100) NOT NULL
                                CHECK (BTRIM(author) <> ''),

                            created_at TIMESTAMPTZ NOT NULL
                                DEFAULT CURRENT_TIMESTAMP,

                            CONSTRAINT fk_ticket_messages_ticket
                                FOREIGN KEY (ticket_id)
                                REFERENCES {}.tickets(ticket_id)
                                ON DELETE CASCADE
                        )
                    """).format(
                        sql.Identifier(schema_name),
                        sql.Identifier(schema_name)
                    )
                )

                # Indexes useful for filtering and retrieving messages.
                cur.execute(
                    sql.SQL("""
                        CREATE INDEX IF NOT EXISTS idx_tickets_status
                        ON {}.tickets (status)
                    """).format(sql.Identifier(schema_name))
                )

                cur.execute(
                    sql.SQL("""
                        CREATE INDEX IF NOT EXISTS idx_tickets_priority
                        ON {}.tickets (priority)
                    """).format(sql.Identifier(schema_name))
                )

                cur.execute(
                    sql.SQL("""
                        CREATE INDEX IF NOT EXISTS idx_messages_ticket_id
                        ON {}.ticket_messages (ticket_id)
                    """).format(sql.Identifier(schema_name))
                )

                conn.commit()
                return True

    except Exception as exc:
        st.error(f"Error initializing Lakebase: {exc}")
        return False


def add_todo(task):
    with get_connection() as conn:
        with conn.cursor() as cur:
            schema = get_schema_name()
            cur.execute(sql.SQL("INSERT INTO {}.todos (task) VALUES (%s)").format(sql.Identifier(schema)), (task.strip(),))
            conn.commit()


def get_todos():
    with get_connection() as conn:
        with conn.cursor() as cur:
            schema = get_schema_name()
            cur.execute(sql.SQL("SELECT id, task, completed, created_at FROM {}.todos ORDER BY created_at DESC").format(sql.Identifier(schema)))
            return cur.fetchall()


def toggle_todo(todo_id):
    with get_connection() as conn:
        with conn.cursor() as cur:
            schema = get_schema_name()
            cur.execute(sql.SQL("UPDATE {}.todos SET completed = NOT completed WHERE id = %s").format(sql.Identifier(schema)), (todo_id,))
            conn.commit()


def delete_todo(todo_id):
    with get_connection() as conn:
        with conn.cursor() as cur:
            schema = get_schema_name()
            cur.execute(sql.SQL("DELETE FROM {}.todos WHERE id = %s").format(sql.Identifier(schema)), (todo_id,))
            conn.commit()

@st.fragment
def display_todos():
    st.subheader("Your Todos")

    todos = get_todos()

    if not todos:
        st.info("No todos yet! Add one above to get started.")
    else:
        for todo_id, task, completed, created_at in todos:
            col1, col2, col3 = st.columns([0.1, 0.7, 0.2])

            with col1:
                if st.checkbox("", value=completed, key=f"check_{todo_id}"):
                    if not completed:
                        toggle_todo(todo_id)
                        st.rerun(scope="fragment")
                elif completed:
                    toggle_todo(todo_id)
                    st.rerun(scope="fragment")

            with col2:
                st.markdown(f"~~{task}~~" if completed else task)
                st.caption(f"Created: {created_at.strftime('%Y-%m-%d %H:%M')}")

            with col3:
                if st.button("Delete", key=f"delete_{todo_id}"):
                    delete_todo(todo_id)
                    st.rerun(scope="fragment")

def get_database_diagnostics():
    """Return database, user, schema and available app tables."""

    with get_connection() as conn:
        with conn.cursor() as cur:
            schema_name = get_schema_name()

            cur.execute("""
                SELECT
                    current_database(),
                    current_user
            """)

            database_name, database_user = cur.fetchone()

            cur.execute("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = %s
                ORDER BY table_name
            """, (schema_name,))

            tables = [row[0] for row in cur.fetchall()]

            return {
                "database": database_name,
                "database_user": database_user,
                "schema": schema_name,
                "tables": tables
            }

            
# Streamlit UI
def main():
    st.set_page_config(
        page_title="Support Ticket App - Phase 4",
        layout="wide"
    )

    st.title("Support Ticket App - Phase 4")
    st.markdown("---")

    # Initialize database
    if not init_database():
        st.stop()

    st.caption(f"Lakebase schema: `{get_schema_name()}`")

    with st.expander("Database diagnostics"):
        st.json(get_database_diagnostics())

    # Add new todo section
    st.subheader("Add New Todo")
    with st.form("add_todo_form", clear_on_submit=True):
        new_task = st.text_input("Enter a new task:", placeholder="What do you need to do?")
        submitted = st.form_submit_button("Add Todo", type="primary")

        if submitted and new_task.strip():
            if add_todo(new_task.strip()):
                st.success("Todo added successfully!")

    st.markdown("---")

    display_todos()

if __name__ == "__main__":
    main()