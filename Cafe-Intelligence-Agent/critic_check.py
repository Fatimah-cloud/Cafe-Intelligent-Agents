from agents.critic import critic_agent

fake_state = {
    'revision_count': 0,
    'findings': [
        {'agent': 'sales', 'claim': 'Spanish Latte is the best seller', 'number': 172.0, 'evidence': 'sum of line_total_sar'},
        {'agent': 'anomaly', 'claim': 'Something felt off this week', 'number': None, 'evidence': ''},
        {'agent': 'operations', 'claim': 'Overall conversion rate (transactions / footfall)', 'number': 145.0, 'evidence': 'buggy calc'},
    ],
}
result = critic_agent(fake_state)
print('critic_target:', result['critic_target'])
print('critic_feedback:', result['critic_feedback'])
print('verified_findings kept:', len(result['verified_findings']))
print('rejection_log:')
for line in result['rejection_log']:
    print(' -', line)