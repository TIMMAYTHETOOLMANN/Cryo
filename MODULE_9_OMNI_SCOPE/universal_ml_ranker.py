"""
Enhanced ML Ranker with Probabilistic Scoring & Cross-Module Intelligence
"""

class UniversalMLRanker:
    def __init__(self):
        self.quality_model = QualityScoringModel()
        self.competition_estimator = CompetitionAnalyzer()
        self.execution_complexity_evaluator = ComplexityScorer()
        
    async def rank_opportunities_continuously(self):
        """Continuously rank opportunities from ALL detector arrays"""
        
        async for opportunity_batch in self._get_unified_opportunity_stream():
            ranked_opportunities = []
            
            for opportunity in opportunity_batch:
                # Your enhanced probabilistic scoring
                quality_score = await self._calculate_quality_score(opportunity)
                competition_score = await self._estimate_competition(opportunity)
                complexity_score = self._evaluate_complexity(opportunity)
                
                # Combined ranking score
                overall_score = (
                    quality_score['expected_value'] * 
                    quality_score['probability'] * 
                    (1 - competition_score) / 
                    complexity_score
                )
                
                ranked_opportunities.append({
                    **opportunity,
                    'overall_score': overall_score,
                    'quality_breakdown': {
                        'expected_value': quality_score['expected_value'],
                        'probability': quality_score['probability'],
                        'competition': competition_score,
                        'complexity': complexity_score
                    }
                })
            
            # Sort by overall score and route to execution
            sorted_opportunities = sorted(
                ranked_opportunities, 
                key=lambda x: -x['overall_score']
            )
            
            await self._route_to_execution(sorted_opportunities[:10])  # Top 10
    
    async def _calculate_quality_score(self, opportunity):
        """Your enhanced quality scoring with ML"""
        features = self._extract_features(opportunity)
        return await self.quality_model.predict(features)
