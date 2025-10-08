"""
Dataset class for storing and validating dataset specifications.
"""
import re
from typing import Dict, List, Any, Optional


class Dataset:
    """
    Represents a dataset specification with features, target, and metadata.
    """
    
    def __init__(self, config: Dict[str, Any]):
        """
        Initialize Dataset from configuration dictionary.
        
        Args:
            config: Dictionary containing dataset_config
        """
        if 'dataset_config' not in config:
            raise ValueError("Configuration must contain 'dataset_config' key")
        
        self.config = config['dataset_config']
        self.name = self.config.get('name')
        self.description = self.config.get('description', '')
        self.random_seed = self.config.get('random_seed')
        self.n_rows = self.config.get('n_rows')
        self.correlations = self.config.get('correlations', [])
        self.features = self.config.get('features', [])
        self.target = self.config.get('target', {})
    
    def validate(self) -> Dict[str, Any]:
        """
        Validate the dataset configuration.
        
        Returns:
            Dictionary with 'valid' boolean and 'errors' list
        """
        errors = []
        
        # Validate required fields
        if not self.name:
            errors.append("Missing required field: dataset_config.name")
        elif not self._is_valid_identifier(self.name):
            errors.append(f"Invalid name '{self.name}': must be valid Python identifier")
        
        if not self.n_rows or self.n_rows <= 0:
            errors.append("n_rows must be positive integer")
        
        if not self.features:
            errors.append("At least one feature is required")
        
        if not self.target:
            errors.append("Target configuration is required")
        
        # Validate features
        feature_names = set()
        for i, feature in enumerate(self.features):
            feature_errors = self._validate_feature(feature, i)
            errors.extend(feature_errors)
            
            # Check for duplicate names
            fname = feature.get('name')
            if fname:
                if fname in feature_names:
                    errors.append(f"Duplicate feature name: {fname}")
                feature_names.add(fname)
        
        # Collect all feature names including lagged versions
        all_feature_names = self._get_all_feature_names()
        
        # Validate target
        target_errors = self._validate_target(self.target, all_feature_names)
        errors.extend(target_errors)
        
        # Validate correlations
        correlation_errors = self._validate_correlations(self.correlations, feature_names)
        errors.extend(correlation_errors)
        
        return {
            'valid': len(errors) == 0,
            'errors': errors
        }
    
    def _is_valid_identifier(self, name: str) -> bool:
        """Check if name is valid Python identifier."""
        return name and re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', name) is not None
    
    def _get_all_feature_names(self) -> set:
        """Get all feature names including lagged versions."""
        all_names = set()
        for feature in self.features:
            fname = feature.get('name')
            if fname:
                all_names.add(fname)
                # Add lagged versions if lags are specified
                lags = feature.get('lags', [])
                for lag in lags:
                    all_names.add(f"{fname}_lag{lag}")
        return all_names
    
    def _validate_feature(self, feature: Dict[str, Any], index: int) -> List[str]:
        """Validate a single feature configuration."""
        errors = []
        prefix = f"Feature {index}"
        
        # Required fields
        name = feature.get('name')
        if not name:
            errors.append(f"{prefix}: missing 'name'")
        elif not self._is_valid_identifier(name):
            errors.append(f"{prefix}: invalid name '{name}'")
        else:
            prefix = f"Feature '{name}'"
        
        data_type = feature.get('data_type')
        if not data_type:
            errors.append(f"{prefix}: missing 'data_type'")
        elif data_type not in ['float', 'int', 'categorical', 'datetime']:
            errors.append(f"{prefix}: invalid data_type '{data_type}'")

        distribution = feature.get('distribution')
        if not distribution:
            errors.append(f"{prefix}: missing 'distribution'")
        else:
            # Validate datetime type requires sequential_datetime distribution
            dist_type = distribution.get('type')
            if data_type == 'datetime' and dist_type != 'sequential_datetime':
                errors.append(f"{prefix}: datetime data_type requires distribution type 'sequential_datetime'")
            if dist_type == 'sequential_datetime' and data_type != 'datetime':
                errors.append(f"{prefix}: sequential_datetime distribution requires data_type 'datetime'")

            dist_errors = self._validate_distribution(distribution, prefix)
            errors.extend(dist_errors)
        
        # Validate categorical requirements
        if data_type == 'categorical':
            categories = feature.get('categories')
            if not categories:
                errors.append(f"{prefix}: categorical type requires 'categories' array")
            elif len(categories) != 10:
                errors.append(f"{prefix}: categories array must have exactly 10 labels")
        
        # Validate rates
        missing_rate = feature.get('missing_rate', 0.0)
        if not (0 <= missing_rate <= 1):
            errors.append(f"{prefix}: missing_rate must be between 0 and 1")
        
        outlier_rate = feature.get('outlier_rate', 0.0)
        if not (0 <= outlier_rate <= 1):
            errors.append(f"{prefix}: outlier_rate must be between 0 and 1")
        
        # Validate outlier method if outlier_rate > 0
        if outlier_rate > 0:
            outlier_method = feature.get('outlier_method')
            if outlier_method and outlier_method not in ['extreme_high', 'extreme_low', 'extreme_both']:
                errors.append(f"{prefix}: invalid outlier_method '{outlier_method}'")
        
        # Validate lags if present
        lags = feature.get('lags', [])
        if lags:
            if not isinstance(lags, list):
                errors.append(f"{prefix}: lags must be a list")
            else:
                for lag in lags:
                    if not isinstance(lag, int) or lag <= 0:
                        errors.append(f"{prefix}: lag values must be positive integers")
        
        return errors
    
    def _validate_distribution(self, dist: Dict[str, Any], prefix: str) -> List[str]:
        """Validate distribution configuration."""
        errors = []
        
        dist_type = dist.get('type')
        if not dist_type:
            errors.append(f"{prefix}: distribution missing 'type'")
            return errors
        
        # Type-specific validation
        if dist_type == 'uniform':
            min_val = dist.get('min')
            max_val = dist.get('max')
            if min_val is None or max_val is None:
                errors.append(f"{prefix}: uniform requires 'min' and 'max'")
            elif min_val >= max_val:
                errors.append(f"{prefix}: uniform min must be less than max")
        
        elif dist_type == 'normal':
            mean = dist.get('mean')
            std = dist.get('std')
            if mean is None or std is None:
                errors.append(f"{prefix}: normal requires 'mean' and 'std'")
            elif std <= 0:
                errors.append(f"{prefix}: normal std must be positive")
        
        elif dist_type == 'weibull':
            shape = dist.get('shape')
            scale = dist.get('scale')
            if shape is None or scale is None:
                errors.append(f"{prefix}: weibull requires 'shape' and 'scale'")
            elif shape <= 0 or scale <= 0:
                errors.append(f"{prefix}: weibull shape and scale must be positive")
        
        elif dist_type == 'random_walk':
            start = dist.get('start')
            step_size = dist.get('step_size')
            if start is None or step_size is None:
                errors.append(f"{prefix}: random_walk requires 'start' and 'step_size'")
            elif step_size <= 0:
                errors.append(f"{prefix}: random_walk step_size must be positive")
        
        elif dist_type == 'sequential':
            start = dist.get('start')
            step = dist.get('step')
            if start is None or step is None:
                errors.append(f"{prefix}: sequential requires 'start' and 'step'")
            elif step == 0:
                errors.append(f"{prefix}: sequential step cannot be zero")

        elif dist_type == 'sequential_datetime':
            start = dist.get('start')
            interval = dist.get('interval')
            if not start:
                errors.append(f"{prefix}: sequential_datetime requires 'start' (ISO datetime string)")
            if not interval:
                errors.append(f"{prefix}: sequential_datetime requires 'interval'")
            elif interval not in ['hourly', 'daily', 'weekly', 'monthly', 'quarterly', 'yearly']:
                errors.append(f"{prefix}: sequential_datetime interval must be one of: hourly, daily, weekly, monthly, quarterly, yearly")

        else:
            errors.append(f"{prefix}: unknown distribution type '{dist_type}'")
        
        return errors
    
    def _validate_target(self, target: Dict[str, Any], feature_names: set) -> List[str]:
        """Validate target configuration."""
        errors = []
        prefix = "Target"
        
        # Required fields
        name = target.get('name')
        if not name:
            errors.append(f"{prefix}: missing 'name'")
        elif not self._is_valid_identifier(name):
            errors.append(f"{prefix}: invalid name '{name}'")
        
        data_type = target.get('data_type')
        if not data_type:
            errors.append(f"{prefix}: missing 'data_type'")
        elif data_type not in ['float', 'int', 'categorical']:
            errors.append(f"{prefix}: invalid data_type '{data_type}'")
        
        expression = target.get('expression')
        if not expression:
            errors.append(f"{prefix}: missing 'expression'")
        else:
            # Validate expression references valid features
            expr_errors = self._validate_expression(expression, feature_names)
            errors.extend([f"{prefix}: {e}" for e in expr_errors])
        
        # Validate categorical requirements
        if data_type == 'categorical':
            categories = target.get('categories')
            if not categories:
                errors.append(f"{prefix}: categorical type requires 'categories' array")
            elif len(categories) != 10:
                errors.append(f"{prefix}: categories array must have exactly 10 labels")
        
        # Validate noise
        noise_percent = target.get('noise_percent', 0.0)
        if not (0 <= noise_percent <= 100):
            errors.append(f"{prefix}: noise_percent must be between 0 and 100")
        
        # Validate seasonality multipliers if present
        seasonality = target.get('seasonality_multipliers', [])
        if seasonality:
            if not isinstance(seasonality, list):
                errors.append(f"{prefix}: seasonality_multipliers must be a list")
            elif len(seasonality) == 0:
                errors.append(f"{prefix}: seasonality_multipliers cannot be empty")
            else:
                for i, mult in enumerate(seasonality):
                    if not isinstance(mult, (int, float)):
                        errors.append(f"{prefix}: seasonality_multipliers[{i}] must be numeric")

        # Validate secondary seasonality multipliers if present
        secondary_seasonality = target.get('secondary_seasonality_multipliers', [])
        if secondary_seasonality:
            if not isinstance(secondary_seasonality, list):
                errors.append(f"{prefix}: secondary_seasonality_multipliers must be a list")
            elif len(secondary_seasonality) == 0:
                errors.append(f"{prefix}: secondary_seasonality_multipliers cannot be empty")
            else:
                for i, mult in enumerate(secondary_seasonality):
                    if not isinstance(mult, (int, float)):
                        errors.append(f"{prefix}: secondary_seasonality_multipliers[{i}] must be numeric")

        return errors
    
    def _validate_expression(self, expression: str, feature_names: set) -> List[str]:
        """Validate that expression only references valid features."""
        errors = []
        
        # Extract potential variable names from expression
        # This is a simple regex that catches word characters
        potential_vars = re.findall(r'\b[a-zA-Z_][a-zA-Z0-9_]*\b', expression)
        
        # Filter out known safe names (numpy functions, python keywords)
        safe_names = {
            'np', 'exp', 'log', 'sqrt', 'sin', 'cos', 'tan', 'abs', 
            'pow', 'min', 'max', 'sum', 'round', 'floor', 'ceil'
        }
        
        invalid_vars = []
        for var in potential_vars:
            if var not in safe_names and var not in feature_names:
                invalid_vars.append(var)
        
        if invalid_vars:
            errors.append(f"expression references undefined features: {', '.join(set(invalid_vars))}")
        
        return errors
    
    def _validate_correlations(self, correlations: List[Dict[str, Any]], feature_names: set) -> List[str]:
        """Validate correlation configurations."""
        errors = []
        
        for i, corr in enumerate(correlations):
            prefix = f"Correlation {i}"
            
            variables = corr.get('variables')
            if not variables:
                errors.append(f"{prefix}: missing 'variables'")
                continue
            
            if len(variables) != 2:
                errors.append(f"{prefix}: 'variables' must contain exactly 2 feature names")
                continue
            
            # Check that variables exist
            for var in variables:
                if var not in feature_names:
                    errors.append(f"{prefix}: unknown feature '{var}'")
            
            # Validate correlation coefficient
            correlation = corr.get('correlation')
            if correlation is None:
                errors.append(f"{prefix}: missing 'correlation' coefficient")
            elif not (-1 <= correlation <= 1):
                errors.append(f"{prefix}: correlation must be between -1 and 1")
            
            # Validate method
            method = corr.get('method')
            if not method:
                errors.append(f"{prefix}: missing 'method'")
            elif method != 'cholesky':
                errors.append(f"{prefix}: only 'cholesky' method is supported")
        
        return errors
    
    def get_feature_names(self) -> List[str]:
        """Get list of all feature names."""
        return [f['name'] for f in self.features if 'name' in f]
    
    def get_feature_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """Get feature configuration by name."""
        for feature in self.features:
            if feature.get('name') == name:
                return feature
        return None
    
    def __repr__(self) -> str:
        return f"Dataset(name='{self.name}', features={len(self.features)}, rows={self.n_rows})"