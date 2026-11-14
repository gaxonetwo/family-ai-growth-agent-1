"""Run beginner-friendly SQL examples against the generated demo database."""
from pprint import pprint
from src.tools.sql_query_tool import SQLQueryTool


def main():
    tool = SQLQueryTool()
    examples = [
        (
            "1) How many registered users?",
            "SELECT COUNT(*) AS total_users FROM users",
        ),
        (
            "2) How many users have bound a device?",
            "SELECT COUNT(DISTINCT user_id) AS users_with_device FROM devices",
        ),
        (
            "3) Which acquisition channels bring users?",
            """SELECT acquisition_channel, COUNT(*) AS user_count
               FROM users
               GROUP BY acquisition_channel
               ORDER BY user_count DESC""",
        ),
        (
            "4) What are the most common behavior events?",
            """SELECT event_name, COUNT(*) AS event_count
               FROM events
               GROUP BY event_name
               ORDER BY event_count DESC""",
        ),
        (
            "5) How many payment records exist by status?",
            """SELECT payment_status, COUNT(*) AS order_count,
                      ROUND(SUM(amount), 2) AS amount_sum
               FROM payments
               GROUP BY payment_status
               ORDER BY order_count DESC""",
        ),
    ]

    for title, sql in examples:
        print("\n" + "=" * 72)
        print(title)
        print("SQL:")
        print(sql)
        print("Result:")
        pprint(tool.run(sql), sort_dicts=False)


if __name__ == "__main__":
    main()
