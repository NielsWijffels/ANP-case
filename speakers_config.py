"""
Gemeente Ranst - Speaker Configuration
Voorgeconfigureerde sprekers voor automatische labeling
"""

KNOWN_SPEAKERS = {
    # Map speaker labels (zelfs maar deels) naar echte namen
    # Voorbeeld: als "Speaker 1" overeenkomt met bepaalde kenmerken → "Burgemeester Jan"
    
    # Je kunt hier gemeente leden toevoegen
    # "Speaker 1": "Burgemeester",
    # "Speaker 2": "Raadslid Marie",
}

MUNICIPALITY_INFO = {
    'name': 'Gemeente Ranst',
    'website': 'https://www.ranst.be',
    'type': 'raadsvergadering',  # raadsvergadering, persconferentie, etc.
}

# Wanneer je de leden weet, voeg ze hier toe:
GEMEENTERAAD_MEMBERS_SAMPLE = [
    # {'name': 'Jan Pietemaet', 'role': 'Burgemeester', 'party': 'N-VA'},
    # {'name': 'Marie Jans', 'role': 'Schepen', 'party': 'Groen'},
]
